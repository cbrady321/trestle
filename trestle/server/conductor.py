"""Execute port — Conductor drives WorkOrder via wrapper spawn."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from trestle.common import clock
from trestle.common.fsutil import atomic_write_json
from trestle.common.ids import generate_artifact_id
from trestle.common.types import WorkOrder
from trestle.server.ledger import RunLedger, evidence_dir, ledger_path, work_dir
from trestle.server.procident import Attribution, GroupStop, Identity, ProcessSource, stop_group
from trestle.server.runs import RunRegistry, cancel_flag_path
from trestle.server.scheduler import Scheduler


@dataclass
class Conductor:
    home: Path
    scheduler: Scheduler
    run_registry: RunRegistry
    process_source: ProcessSource | None = None
    # B2-C10's kill: the one stopper, at both call sites; a test injects its own
    stopper: Callable[[Attribution], GroupStop] = stop_group

    def drive(self, order: WorkOrder) -> str:
        run_dir = self._find_run_dir(order.run_id)
        ledger = RunLedger.open(ledger_path(run_dir))
        ledger.append("admitted", run_id=order.run_id, snapshot_id=order.snapshot_id)
        ledger.append("started", run_id=order.run_id)

        spec_path = evidence_dir(run_dir) / "spec.json"
        timeout_s = 300
        if spec_path.exists():
            spec = json.loads(spec_path.read_text(encoding="utf-8"))
            timeout_s = int(spec.get("timeout_s", timeout_s))

        wrapper_cmd = [
            sys.executable,
            "-m",
            "trestle.wrapper.main",
            "--run-dir",
            str(run_dir),
        ]
        started = time.monotonic()
        proc = subprocess.Popen(
            wrapper_cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=_subprocess_env(self.home),
            start_new_session=True,
        )
        # B2-C16: the leader's identity row goes down after spawn and before the first liveness
        # poll; the wrapper does not wait on it.
        attribution = Attribution(
            group=proc.pid,
            record=lambda ident: _record_identity(ledger, order.run_id, ident),
            source=self.process_source,
        )
        attribution.attribute_leader(proc.pid)
        self.run_registry.register(order.run_id, proc, attribution)
        cancel_flag = cancel_flag_path(run_dir)
        deadline = time.monotonic() + timeout_s
        stop_cause: str | None = None
        try:
            while proc.poll() is None:
                attribution.observe()
                if cancel_flag.exists():
                    stop_cause = "cancel"
                    self.stopper(attribution)
                    break
                if time.monotonic() > deadline:
                    stop_cause = "deadline"
                    self.stopper(attribution)
                    break
                time.sleep(clock.poll_interval)
            if stop_cause is None:  # the exit was observed: check once more (B2-C10)
                if cancel_flag.exists():
                    stop_cause = "cancel"
                elif time.monotonic() > deadline:
                    stop_cause = "deadline"
        finally:
            self.run_registry.unregister(order.run_id)
            # a request-path stop still in flight finishes before this run appends another row
            with attribution.lock:
                attribution.close()
            if proc.stdout is not None:
                proc.stdout.close()
            if proc.stderr is not None:
                proc.stderr.close()
            proc.wait()

        duration_ms = int((time.monotonic() - started) * 1000)

        report_path = evidence_dir(run_dir) / "wrapper_report.json"
        classification = "failed"
        exit_code = proc.returncode if proc.returncode is not None else 1
        wrapper_limits: list[dict[str, object]] = []
        report: dict[str, object] = {}
        if report_path.exists():
            loaded = json.loads(report_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                report = loaded
        raw_limits = report.get("limits_exceeded", [])
        if isinstance(raw_limits, list):
            wrapper_limits = [item for item in raw_limits if isinstance(item, dict)]
        if stop_cause == "cancel":
            classification = "cancelled"
        elif stop_cause == "deadline":
            classification = "timed_out"
        elif report:
            classification = str(report.get("classification", classification))
            exit_code = _as_int(report.get("exit_code", exit_code), exit_code)
        elif proc.returncode == 0:
            classification = "succeeded"

        artifact_ids = self._promote_outputs(run_dir, ledger, order.run_id)
        child_limits = _read_ndjson(evidence_dir(run_dir) / "capture_limits.ndjson")
        limits_exceeded = _merge_limit_markers(wrapper_limits + child_limits)
        if limits_exceeded:
            ledger.append("limit_exceeded", run_id=order.run_id, markers=limits_exceeded)

        result_path = evidence_dir(run_dir) / "result.json"
        index_path = evidence_dir(run_dir) / "result.index"
        result_state = "absent"
        if (evidence_dir(run_dir) / "result.state").exists():
            result_state = "too_large"
        elif result_path.exists() and index_path.exists():
            result_state = "complete"
        elif result_path.exists():
            result_state = "invalid"

        meta = {
            "run_id": order.run_id,
            "classification": classification,
            "duration_ms": duration_ms,
            "result_state": result_state,
            "artifact_count": len(artifact_ids),
            "limits_exceeded": limits_exceeded or None,
        }
        atomic_write_json(evidence_dir(run_dir) / "meta.json", meta)

        ledger.append(
            "execution_ended",
            run_id=order.run_id,
            classification=classification,
            exit_code=exit_code,
            duration_ms=duration_ms,
        )

        completeness = "partial" if limits_exceeded else "complete"
        ledger.append(
            "evidence_finalized",
            run_id=order.run_id,
            completeness=completeness,
            result_state=result_state,
        )
        ledger.append(classification, run_id=order.run_id)

        self.scheduler.complete(order.run_id)
        return classification

    async def drive_async(self, order: WorkOrder) -> str:
        """Async entry — runs sync drive on a worker thread (wrapper stays sync)."""
        return await asyncio.to_thread(self.drive, order)

    def _promote_outputs(self, run_dir: Path, ledger: RunLedger, run_id: str) -> list[str]:
        outputs_dir = work_dir(run_dir) / "outputs"
        if not outputs_dir.exists():
            return []
        artifact_ids: list[str] = []
        for path in sorted(outputs_dir.iterdir()):
            if not path.is_file():
                continue
            art_id = generate_artifact_id()
            dest = evidence_dir(run_dir) / "artifacts" / art_id
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)
            ledger.append(
                "artifact_available",
                run_id=run_id,
                artifact_id=art_id,
                name=path.name,
                source="auto_promote",
            )
            artifact_ids.append(art_id)
        return artifact_ids

    def _find_run_dir(self, run_id: str) -> Path:
        runs_root = self.home / "runs"
        if not runs_root.exists():
            raise FileNotFoundError(run_id)
        for month_dir in runs_root.iterdir():
            candidate = month_dir / run_id
            if candidate.is_dir():
                return candidate
        raise FileNotFoundError(run_id)


def _record_identity(ledger: RunLedger, run_id: str, ident: Identity) -> None:
    ledger.append("process_identity", run_id=run_id, **ident.fields())


def _read_ndjson(path: Path) -> list[dict[str, object]]:
    if not path.exists():
        return []
    out: list[dict[str, object]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        if isinstance(item, dict):
            out.append(item)
    return out


def _as_int(value: object, default: int = 0) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        return int(value)
    return default


def _merge_limit_markers(markers: list[dict[str, object]]) -> list[dict[str, object]]:
    merged: dict[tuple[str, str], dict[str, object]] = {}
    for marker in markers:
        stream = str(marker.get("stream", "unknown"))
        limit = str(marker.get("limit", "unknown"))
        key = (stream, limit)
        recorded = _as_int(marker.get("bytes_recorded", 0))
        suppressed = _as_int(marker.get("bytes_suppressed", 0))
        if key in merged:
            merged[key]["bytes_recorded"] = _as_int(merged[key]["bytes_recorded"]) + recorded
            merged[key]["bytes_suppressed"] = _as_int(merged[key]["bytes_suppressed"]) + suppressed
        else:
            merged[key] = {
                "stream": stream,
                "limit": limit,
                "bytes_recorded": recorded,
                "bytes_suppressed": suppressed,
            }
    return list(merged.values())


def _subprocess_env(home: Path) -> dict[str, str]:
    import os

    env = os.environ.copy()
    root = str(Path(__file__).resolve().parents[2])
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = root if not existing else f"{root}{os.pathsep}{existing}"
    env["TRESTLE_HOME"] = str(home)
    return env
