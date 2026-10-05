"""Execute port — Conductor drives WorkOrder via wrapper spawn."""

from __future__ import annotations

import asyncio
import json
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from trestle.common import clock, codes, redact
from trestle.common.errtext import sanitize
from trestle.common.fsutil import atomic_write, atomic_write_json
from trestle.common.ids import generate_artifact_id
from trestle.common.limits import capture_limits
from trestle.common.plan.compiler import AdmittedPlan
from trestle.common.pyenv import build_child_env, python_argv
from trestle.common.types import WorkOrder
from trestle.server import answer, fold, sweep
from trestle.server.config import load_config
from trestle.server.ledger import RunLedger, evidence_dir, ledger_path, work_dir
from trestle.server.procident import Attribution, GroupStop, Identity, ProcessSource, stop_group
from trestle.server.projection import count_events
from trestle.server.runs import RunRegistry, cancel_flag_path, release_point_flag_path
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

    def __post_init__(self) -> None:
        # B2-C12: a cancel written for a run still waiting in the queue finalizes it there
        self.run_registry.on_cancel_flag = self._cancel_flag_written

    def _cancel_flag_written(self, run_id: str) -> None:
        self.scheduler.cancel_waiting(
            run_id, lambda order: self.finalize_queued(order, fold.CAUSE_CANCEL)
        )

    def drive(self, order: WorkOrder) -> str:
        try:
            return self._drive(order)
        finally:
            # the scheduler's slot, and with it the run's environment key (WR-OWN-8), is released on
            # every exit, a raised exception included: on a normal exit after the terminal row
            # (which ends the lease even when the stop was unconfirmed, OQ-34), so the next run
            # waiting on the key starts only after this run's answer is durable
            self.scheduler.complete(order.run_id)

    def admitted_deadline(self, order: WorkOrder) -> float:
        """The run's admitted deadline on the monotonic clock (B2-C5), read from its spec."""
        return _monotonic_deadline(self._read_spec(self._find_run_dir(order.run_id)))

    def finalize_unspawned(self, order: WorkOrder) -> str:
        """End a run that reached its deadline while queued, without ever starting it (B2-C5,
        B2-C12): the scheduler's expiry callback."""
        return self.finalize_queued(order, fold.CAUSE_RELEASE_POINT)

    def finalize_queued(self, order: WorkOrder, cause: str) -> str:
        """End a run that was stopped before any process was spawned (B2-C12): a cancel, or its
        deadline passing, while it waited. U2 appends `StopRow(cause, lane_committed_length=0)`
        and finalizes `cancelled` / `timed_out` with no process, lane, kill, fold or sweep; the
        run has no `started`, `process_identity` or `group_stop` row, since no process group
        exists to stop, and the answer reads it as an empty fold, an empty cleanup and a
        confirmed-gone group (CB-6). The rows follow a spawned run's order after the stop row:
        `error_record`, then `evidence_finalized`, then the terminal row."""
        run_dir = self._find_run_dir(order.run_id)
        ledger = RunLedger.open(ledger_path(run_dir))
        fold.record_stop(run_dir, ledger, cause, queued=True)
        if cause == fold.CAUSE_CANCEL:
            classification = "cancelled"
            error = _composed(
                codes.EXECUTION_CANCELLED,
                "queue",
                "the run was cancelled while queued and was never started",
            )
        else:
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
            event_count=0,
        )
        self._finalize_answer(run_dir, ledger, classification)
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
        spec = self._read_spec(run_dir)
        # a stop that came before the run was spawned (a cancel written between dispatch and here,
        # or a deadline that fell while it waited) ends it as a queued run: nothing to stop
        if cancel_flag_path(run_dir).exists():
            return self.finalize_queued(order, fold.CAUSE_CANCEL)
        if time.monotonic() >= _monotonic_deadline(spec):
            return self.finalize_queued(order, fold.CAUSE_RELEASE_POINT)

        ledger = RunLedger.open(ledger_path(run_dir))
        ledger.append("admitted", run_id=order.run_id, snapshot_id=order.snapshot_id)
        ledger.append("started", run_id=order.run_id)

        wrapper_cmd = python_argv("-m", "trestle.wrapper.main", "--run-dir", str(run_dir))
        env = build_child_env(home=self.home)
        env.pop(redact.SECRETS_ENV, None)  # only this run's own values, never an inherited variable
        if order.secrets:  # MC-CORE-13: the real values go to the wrapper and child by environment
            env[redact.SECRETS_ENV] = redact.encode_env(order.secrets)
        secrets = redact.secret_strings(order.secrets)
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
        release_slice = _release_slice(spec)
        release_point = deadline - release_slice
        stop: GroupStop | None = None
        stop_row_at: float | None = None  # U2 recorded a stop (B2-C15) at this monotonic moment

        def record_stop(cause: str) -> None:
            nonlocal stop_row_at
            fold.record_stop(run_dir, ledger, cause)
            stop_row_at = time.monotonic()

        release_flag = release_point_flag_path(run_dir)
        try:
            while proc.poll() is None:
                attribution.observe()
                now = time.monotonic()
                if stop_row_at is None:
                    # B2-C10: the cancel flag, or the release point (U2 writes its flag itself);
                    # a cancel wins when both hold
                    if cancel_flag.exists():
                        record_stop(fold.CAUSE_CANCEL)
                    elif now >= release_point or release_flag.exists():
                        # the root's own slice end raises the flag too (P4): its clock may be
                        # ahead of this one (another clock, a sleep, a clock step)
                        atomic_write(release_flag, b"1")
                        record_stop(fold.CAUSE_RELEASE_POINT)
                if stop_row_at is not None and now >= stop_row_at + release_slice:
                    # the release slice has elapsed with the root still live: kill (B2-C10)
                    stop = self.stopper(attribution)
                    break
                time.sleep(clock.poll_interval)
            if stop_row_at is None:  # the exit was observed: check once more, cancel first
                if cancel_flag.exists():
                    record_stop(fold.CAUSE_CANCEL)
                elif time.monotonic() >= release_point or release_flag.exists():
                    record_stop(fold.CAUSE_RELEASE_POINT)  # no process is left to read a flag
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
                        if succeeded and stop_row_at is None:
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

        # B2-C7 (MC-19): the lane is folded, and its entries are in the ledger as `lane_folded`
        # rows, before the row that ends execution; a run with no lane writes none. `accepted` is
        # spec.plan's admitted plan (None: the implicit one-vertex plan).
        plan = _accepted_plan(spec)
        folded = fold.fold_into_ledger(run_dir, ledger, plan)

        # B2-C10: the sweep runs inside the finalization margin, after the fold and the kill; its
        # rows are durable before the terminal row (B4-C7). A plain plugin has only the process
        # group target, disposed from the group stop above, so it writes none.
        limits = load_config(self.home).operator_limits
        swept = sweep.sweep_detailed(folded, stop, sweep.budget_for(limits), limits, plan)
        sweep.write_rows(ledger, order.run_id, swept)

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
        first_stop = folded.stop_rows[0].cause if folded.stop_rows else None
        if first_stop == fold.CAUSE_CANCEL:  # the class is the first stop row's cause (B2-C15)
            classification = "cancelled"
        elif first_stop == fold.CAUSE_RELEASE_POINT:
            classification = "timed_out"
        elif report:
            classification = str(report.get("classification", classification))
            exit_code = _as_int(report.get("exit_code", exit_code), exit_code)
        elif proc.returncode == 0:
            classification = "succeeded"

        # MC-15: the run's one explanation, in the ledger, before the row that ends execution
        error = _error_fields(self.home, run_dir, classification, exit_code, folded)
        if error is not None:
            # the child scrubbed its message; a file a plugin process wrote is scrubbed again
            error = {key: redact.scrub(value, secrets) for key, value in error.items()}
            ledger.append("error_record", run_id=order.run_id, **error)

        promotion_markers: list[dict[str, object]] = []
        scrubber = redact.Scrubber(secrets=secrets, roots=redact.run_roots(run_dir, self.home))
        artifact_ids = self._promote_outputs(
            run_dir, ledger, order.run_id, scrubber, promotion_markers
        )
        # nothing can write to the work directory now: what the plugin left there is scrubbed too,
        # so the run directory holds no declared secret anywhere (WR-EVID-8)
        redact.scrub_tree(work_dir(run_dir), secrets)
        child_limits = _read_ndjson(evidence_dir(run_dir) / "capture_limits.ndjson")
        limits_exceeded = _merge_limit_markers(wrapper_limits + child_limits + promotion_markers)
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
            event_count=count_events(evidence_dir(run_dir)),
        )
        # B4 Ordering: U2 projects the answer from the durable inputs before the terminal row
        self._finalize_answer(run_dir, ledger, classification)
        ledger.append(classification, run_id=order.run_id)

        return classification

    def _finalize_answer(self, run_dir: Path, ledger: RunLedger, terminal_kind: str) -> None:
        """Persist the answer handed over at finalization (`evidence/answer.json`, what `detail`
        resolves to): the run view recomputes it from the same durable inputs (B4-C1)."""
        spec = self._read_spec(run_dir)
        answer.write_finalized(
            run_dir, answer.answer_for_run(run_dir, ledger.records, terminal_kind, spec)
        )
        answer.write_child_views(run_dir, spec)  # V-1.3: each child view, terminal form

    async def drive_async(self, order: WorkOrder) -> str:
        """Async entry — runs sync drive on a worker thread (wrapper stays sync)."""
        return await asyncio.to_thread(self.drive, order)

    def _promote_outputs(
        self,
        run_dir: Path,
        ledger: RunLedger,
        run_id: str,
        scrubber: redact.Scrubber = redact.NO_SCRUB,
        markers: list[dict[str, object]] | None = None,
    ) -> list[str]:
        """Promote what a run left to artifacts: `outputs/` (auto-promote) and every staged
        `ctx.artifact()` file that was never attached (a staged file is promoted or refused with a
        marker, never left out, WR-EVID-6). Every promoted file passes the write-path scrub
        (MC-CORE-13): text is copied with declared secrets and host paths scrubbed, and a binary
        file holding a secret is not promoted at all: a `secret_in_binary` marker is appended to
        `markers` in its place (nothing is written that later needs scrubbing). The run's count
        and byte caps hold across everything the run has as artifacts, those the child attached
        included: a file that would pass either is not promoted and a marker names the limit."""
        work = work_dir(run_dir)
        candidates: list[tuple[Path, str, str]] = []
        outputs_dir = work / "outputs"
        if outputs_dir.exists():
            candidates += [
                (path, path.name, "auto_promote")
                for path in sorted(outputs_dir.iterdir())
                if path.is_file()
            ]
        staging_dir = work / "artifact-staging"
        if staging_dir.exists():
            candidates += [
                (path, path.relative_to(staging_dir).as_posix().removesuffix(".partial"), "staged")
                for path in sorted(staging_dir.rglob("*.partial"))
                if path.is_file()
            ]

        limits = capture_limits()
        held = [p for p in (evidence_dir(run_dir) / "artifacts").glob("*") if p.is_file()]
        count = len(held)
        total_bytes = sum(p.stat().st_size for p in held)
        artifact_ids: list[str] = []
        for path, name, source in candidates:
            size = path.stat().st_size
            capped = None
            if count >= limits.max_artifact_count:
                capped = "max_artifact_count"
            elif total_bytes + size > limits.max_artifact_bytes:
                capped = "max_artifact_bytes"
            if capped is not None:
                if markers is not None:
                    markers.append(_artifact_marker(capped, size))
                continue
            art_id = generate_artifact_id()
            dest = evidence_dir(run_dir) / "artifacts" / art_id
            dest.parent.mkdir(parents=True, exist_ok=True)
            if redact.copy_scrubbed(path, dest, scrubber):
                if markers is not None:
                    markers.append(_artifact_marker(redact.BINARY_LIMIT, size))
                continue
            ledger.append(
                "artifact_available",
                run_id=run_id,
                artifact_id=art_id,
                name=scrubber.text(name),
                source=source,
            )
            artifact_ids.append(art_id)
            count += 1
            total_bytes += dest.stat().st_size
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


def _accepted_plan(spec: dict[str, object]) -> AdmittedPlan | None:
    """`spec.plan` as the admitted plan, or None (the implicit one-vertex plan) when the spec
    carries none, or one this server cannot read (recovery classifies that, L.SV-3.8)."""
    try:
        return fold.plan_of_spec(spec)
    except ValueError:
        return None


def _release_slice(spec: dict[str, object]) -> float:
    """This root's release slice, fixed at admission (`spec.plan.release_slice`, B2-C2): 0 for a
    spec with no plan or a plan that declares no release walk."""
    plan = spec.get("plan")
    raw = plan.get("release_slice") if isinstance(plan, dict) else None
    return float(raw) if isinstance(raw, (int, float)) and not isinstance(raw, bool) else 0.0


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
    home: Path,
    run_dir: Path,
    classification: str,
    exit_code: int,
    folded: fold.FoldedRecord | None = None,
) -> dict[str, str] | None:
    """The `error_record` fields {code, phase, message} for a run that did not succeed (MC-15).

    A cancel and a deadline are the supervisor's own first cause, so their code comes from the
    class whatever the child managed to write while it was being stopped. A child that ended by
    itself hands over `evidence/child_error.json`, folded here; failing that, the lane's own
    record of the failure (the root's failing step, else the code its NodeEnd carries: DM-02);
    a child that failed without either (or with one that does not parse) is a `worker_exit`.
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
    child = _read_child_error(home, run_dir)
    if child is not None:
        return child
    from_lane = fold.lane_error(folded) if folded is not None else None
    if from_lane is not None:
        return from_lane
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


def _artifact_marker(limit: str, suppressed: int) -> dict[str, object]:
    return {
        "stream": "artifacts",
        "limit": limit,
        "bytes_recorded": 0,
        "bytes_suppressed": suppressed,
    }


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
