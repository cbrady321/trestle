"""Execute port — Conductor drives WorkOrder via wrapper spawn."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from trestle.common.fsutil import atomic_write_json
from trestle.common.ids import generate_artifact_id
from trestle.common.types import WorkOrder
from trestle.server.ledger import RunLedger, evidence_dir, ledger_path, work_dir
from trestle.server.runs import RunRegistry, cancel_flag_path, terminate_process_group
from trestle.server.scheduler import Scheduler


@dataclass
class Conductor:
    home: Path
    scheduler: Scheduler
    run_registry: RunRegistry

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
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=_subprocess_env(self.home),
            start_new_session=True,
        )
        self.run_registry.register(order.run_id, proc)
        cancel_flag = cancel_flag_path(run_dir)
        deadline = time.monotonic() + timeout_s
        try:
            while proc.poll() is None:
                if cancel_flag.exists():
                    terminate_process_group(proc, grace_s=0.0, kill_s=1.0)
                    break
                if time.monotonic() > deadline:
                    terminate_process_group(proc, grace_s=0.0, kill_s=1.0)
                    break
                time.sleep(0.05)
        finally:
            self.run_registry.unregister(order.run_id)
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
        timed_out = time.monotonic() > deadline and not cancel_flag.exists()
        if cancel_flag.exists():
            classification = "cancelled"
        elif report_path.exists():
            report = json.loads(report_path.read_text(encoding="utf-8"))
            classification = str(report.get("classification", classification))
            exit_code = int(report.get("exit_code", exit_code))
            raw_limits = report.get("limits_exceeded", [])
            if isinstance(raw_limits, list):
                wrapper_limits = [item for item in raw_limits if isinstance(item, dict)]
        elif timed_out:
            classification = "timed_out"
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
