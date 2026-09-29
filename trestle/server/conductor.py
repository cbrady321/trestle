"""Execute port — Conductor drives WorkOrder via wrapper spawn."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from trestle.common import clock, codes, redact
from trestle.common.errtext import sanitize
from trestle.common.fsutil import atomic_write_json
from trestle.common.ids import generate_artifact_id
from trestle.common.pyenv import build_child_env, python_argv
from trestle.common.types import WorkOrder
from trestle.server.ledger import RunLedger, evidence_dir, ledger_path, work_dir
from trestle.server.procident import Attribution, GroupStop, Identity, ProcessSource, stop_group
from trestle.server.runs import RunRegistry, cancel_flag_path
from trestle.server.scheduler import Scheduler

# K-8 (OQ-5 recorded default; MC-CORE-12's switch): a run that ended succeeded has its attributable
# processes stopped before finalization, like every other terminal path (B2-C10). The decline
# patch flips this to False; that contradicts B2-C10, so it is applied only after B2-C10 is
# amended, never unattended (F-PLAN-6).
REAP_ON_SUCCESS: bool = True


@dataclass
class Conductor:
    home: Path
    scheduler: Scheduler
    run_registry: RunRegistry
    process_source: ProcessSource | None = None
    # B2-C10's kill: the one stopper, at both call sites; a test injects its own
    stopper: Callable[[Attribution], GroupStop] = stop_group

    def drive(self, order: WorkOrder) -> str:
        try:
            return self._drive(order)
        finally:
            # the scheduler's slot is released on every exit, a raised exception included
            self.scheduler.complete(order.run_id)

    def admitted_deadline(self, order: WorkOrder) -> float:
        """The run's admitted deadline on the monotonic clock (B2-C5), read from its spec."""
        return _monotonic_deadline(self._read_spec(self._find_run_dir(order.run_id)))

    def finalize_unspawned(self, order: WorkOrder) -> str:
        """End a run that reached its deadline while queued, without ever starting it (B2-C5,
        B2-C12): `timed_out` with the deadline error and no `started`, `process_identity` or
        `group_stop` row, since no process group exists to stop. The rows follow the same order as
        a spawned run's: `error_record`, then `evidence_finalized`, then the terminal row."""
        run_dir = self._find_run_dir(order.run_id)
        ledger = RunLedger.open(ledger_path(run_dir))
        classification = "timed_out"
        error = _composed(
            codes.EXECUTION_DEADLINE_EXCEEDED,
            "queue",
            "the run reached its deadline while queued and was never started",
        )
        ledger.append("error_record", run_id=order.run_id, **error)
        atomic_write_json(
            evidence_dir(run_dir) / "meta.json",
            {
                "run_id": order.run_id,
                "classification": classification,
                "duration_ms": _admitted_age_ms(self._read_spec(run_dir)),
                "result_state": "absent",
                "artifact_count": 0,
                "limits_exceeded": None,
                "error": error,
            },
        )
        ledger.append(
            "evidence_finalized",
            run_id=order.run_id,
            completeness="complete",
            result_state="absent",
        )
        ledger.append(classification, run_id=order.run_id)
        return classification

    @staticmethod
    def _read_spec(run_dir: Path) -> dict[str, object]:
        spec_path = evidence_dir(run_dir) / "spec.json"
        if not spec_path.exists():
            return {}
        loaded = json.loads(spec_path.read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, dict) else {}

    def _drive(self, order: WorkOrder) -> str:
        run_dir = self._find_run_dir(order.run_id)
        ledger = RunLedger.open(ledger_path(run_dir))
        ledger.append("admitted", run_id=order.run_id, snapshot_id=order.snapshot_id)
        ledger.append("started", run_id=order.run_id)

        spec = self._read_spec(run_dir)

        wrapper_cmd = python_argv("-m", "trestle.wrapper.main", "--run-dir", str(run_dir))
        env = build_child_env(home=self.home)
        env.pop(redact.SECRETS_ENV, None)  # only this run's own values, never an inherited variable
        if order.secrets:  # MC-CORE-13: the real values go to the wrapper and child by environment
            env[redact.SECRETS_ENV] = redact.encode_env(order.secrets)
        started = time.monotonic()
        proc = subprocess.Popen(
            wrapper_cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
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
        deadline = _monotonic_deadline(spec)
        first_observed_cause: str | None = None
        stop: GroupStop | None = None
        try:
            while proc.poll() is None:
                attribution.observe()
                if cancel_flag.exists():
                    first_observed_cause = "cancel"
                    stop = self.stopper(attribution)
                    break
                if time.monotonic() > deadline:
                    first_observed_cause = "deadline"
                    stop = self.stopper(attribution)
                    break
                time.sleep(clock.poll_interval)
            if first_observed_cause is None:  # the exit was observed: check once more (B2-C10)
                if cancel_flag.exists():
                    first_observed_cause = "cancel"
                elif time.monotonic() > deadline:
                    first_observed_cause = "deadline"
        finally:
            self.run_registry.unregister(order.run_id)
            # B2-C10: the kill runs on every terminal path, a normal exit included. A request-path
            # stop still in flight finishes first (one stop at a time), and nothing signals after.
            with attribution.lock:
                if stop is None:
                    if REAP_ON_SUCCESS:
                        stop = self.stopper(attribution)
                    else:  # K-8 declined: a run that ended succeeded is observed, never signalled
                        try:
                            wrapper_report = evidence_dir(run_dir) / "wrapper_report.json"
                            ended = json.loads(wrapper_report.read_text(encoding="utf-8"))
                            succeeded = ended.get("classification") == "succeeded"
                        except (OSError, ValueError, AttributeError):
                            succeeded = False
                        if succeeded and first_observed_cause is None:
                            stop = GroupStop(
                                confirmed_gone=not attribution.observe(), signalled=False
                            )
                        else:
                            stop = self.stopper(attribution)
                attribution.close()
            if proc.stdout is not None:
                proc.stdout.close()
            if proc.stderr is not None:
                proc.stderr.close()
            try:  # after the stop the leader is gone, or could not be killed (never waited on)
                proc.wait(timeout=clock.kill)
            except subprocess.TimeoutExpired:
                pass

        assert stop is not None
        # MC-32: one group_stop per spawned run, after the kill and before evidence_finalized
        ledger.append(
            "group_stop",
            run_id=order.run_id,
            confirmed_gone=stop.confirmed_gone,
            method=stop.method,
        )

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
        if first_observed_cause == "cancel":
            classification = "cancelled"
        elif first_observed_cause == "deadline":
            classification = "timed_out"
        elif report:
            classification = str(report.get("classification", classification))
            exit_code = _as_int(report.get("exit_code", exit_code), exit_code)
        elif proc.returncode == 0:
            classification = "succeeded"

        # MC-15: the run's one explanation, in the ledger, before the row that ends execution
        error = _error_fields(self.home, run_dir, classification, exit_code)
        if error is not None:
            ledger.append("error_record", run_id=order.run_id, **error)

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
        if error is not None:  # derived from the ledger row, never a second source
            meta["error"] = error
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


def _monotonic_deadline(spec: dict[str, object]) -> float:
    """The moment, on the monotonic clock, the run's admitted deadline falls (B2-C5): the deadline
    `spec.deadline` fixed at admission, not a fresh timeout taken from spawn, so time spent
    queued or held counts against it. A spec with no deadline falls back to its `timeout_s`."""
    raw = spec.get("deadline")
    if isinstance(raw, str):
        try:
            fixed = datetime.fromisoformat(raw)
        except ValueError:
            fixed = None
        if fixed is not None:
            if fixed.tzinfo is None:
                fixed = fixed.replace(tzinfo=UTC)
            return time.monotonic() + (fixed - datetime.now(tz=UTC)).total_seconds()
    return time.monotonic() + _as_int(spec.get("timeout_s", 300), 300)


def _admitted_age_ms(spec: dict[str, object]) -> int:
    """Milliseconds since admission, read back from the spec's deadline minus its budget."""
    raw = spec.get("deadline")
    if isinstance(raw, str):
        try:
            fixed = datetime.fromisoformat(raw)
        except ValueError:
            return 0
        if fixed.tzinfo is None:
            fixed = fixed.replace(tzinfo=UTC)
        admitted = fixed.timestamp() - _as_int(spec.get("timeout_s", 0))
        return max(0, int((time.time() - admitted) * 1000))
    return 0


def _error_fields(
    home: Path, run_dir: Path, classification: str, exit_code: int
) -> dict[str, str] | None:
    """The `error_record` fields {code, phase, message} for a run that did not succeed (MC-15).

    A cancel and a deadline are the supervisor's own first cause, so their code comes from the
    class whatever the child managed to write while it was being stopped. A child that ended by
    itself hands over `evidence/child_error.json`, folded here; a child that failed without one
    (or with one that does not parse) is a `worker_exit`.
    """
    if classification == "succeeded":
        return None
    if classification == "cancelled":
        return _composed(codes.EXECUTION_CANCELLED, "stop", "the run was cancelled")
    if classification == "timed_out":
        return _composed(
            codes.EXECUTION_DEADLINE_EXCEEDED,
            "stop",
            "the run reached its deadline and was stopped",
        )
    folded = _read_child_error(home, run_dir)
    if folded is not None:
        return folded
    return _composed(
        codes.EXECUTION_WORKER_EXIT,
        "exit",
        f"the child exited with status {exit_code} and wrote no error record",
    )


def _composed(code: str, phase: str, message: str) -> dict[str, str]:
    return {"code": code, "phase": phase, "message": message}


def _read_child_error(home: Path, run_dir: Path) -> dict[str, str] | None:
    """The child's error handoff, or None when it is absent or not one. The child sanitized the
    message; it crosses a trust boundary (a plugin process wrote it), so it is bounded again."""
    path = evidence_dir(run_dir) / "child_error.json"
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(loaded, dict):
        return None
    code, phase, message = loaded.get("code"), loaded.get("phase"), loaded.get("message")
    if code not in codes.EXECUTION_CODES or not isinstance(phase, str):
        return None
    if not isinstance(message, str):
        return None
    return {
        "code": str(code),
        "phase": sanitize(phase, {}),  # bounded like the message (PATH_MAX = 512 B)
        "message": sanitize(message, {"home": home, "run": run_dir}),
    }


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
