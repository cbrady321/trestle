"""Crash recovery from ledger authority (R-STORE-18, R-STORE-19)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from trestle.common import codes
from trestle.common.fsutil import atomic_write, atomic_write_json, fsync_dir
from trestle.common.ids import generate_service_epoch
from trestle.server import fold, procident
from trestle.server.ledger import (
    TERMINAL_KINDS,
    RunLedger,
    evidence_dir,
    ledger_path,
    run_dir_for,
    work_dir,
)
from trestle.server.procident import ProcessSource, Signaller

_MID_EXECUTION_KINDS = frozenset(
    {
        "progress",
        "artifact_declared",
        "artifact_available",
        "limit_exceeded",
    }
)


def recover_on_startup(
    home: Path, *, source: ProcessSource | None = None, signaller: Signaller | None = None
) -> str:
    """Sweep run dirs, then mint a new service epoch."""
    home.mkdir(parents=True, exist_ok=True)
    recover_all_runs(home, source=source, signaller=signaller)
    epoch = generate_service_epoch()
    atomic_write(home / "service_epoch", epoch.encode("utf-8"))
    return epoch


def recover_all_runs(
    home: Path, *, source: ProcessSource | None = None, signaller: Signaller | None = None
) -> None:
    runs_root = home / "runs"
    if not runs_root.exists():
        return
    for month_dir in sorted(runs_root.iterdir()):
        if not month_dir.is_dir():
            continue
        for run_dir in sorted(month_dir.iterdir()):
            if run_dir.is_dir():
                recover_run_dir(run_dir, source=source, signaller=signaller)


def recover_run_dir(
    run_dir: Path, *, source: ProcessSource | None = None, signaller: Signaller | None = None
) -> None:
    path = ledger_path(run_dir)
    if not path.exists():
        sweep_run_dir(run_dir)
        return

    ledger = RunLedger.open(path)
    terminal = ledger.terminal_state()
    if terminal is not None and ledger.has_kind("evidence_finalized"):
        rematerialize_meta(run_dir, ledger)
        return

    if not ledger.records:
        sweep_run_dir(run_dir)
        return

    last_kind = str(ledger.records[-1].get("kind", ""))
    if last_kind in TERMINAL_KINDS:
        rematerialize_meta(run_dir, ledger)
        return

    if last_kind == "evidence_finalized":
        if terminal is not None:
            rematerialize_meta(run_dir, ledger)
        else:
            append_recovery_suffix(run_dir, ledger, source=source, signaller=signaller)
        return

    if last_kind == "execution_ended":
        append_recovery_suffix(run_dir, ledger, source=source, signaller=signaller)
        return

    if last_kind in {"created", "admitted", "started"} or last_kind in _MID_EXECUTION_KINDS:
        append_recovery_suffix(run_dir, ledger, source=source, signaller=signaller)
        return

    append_recovery_suffix(run_dir, ledger, source=source, signaller=signaller)


def sweep_run_dir(run_dir: Path) -> None:
    shutil.rmtree(run_dir, ignore_errors=True)
    parent = run_dir.parent
    if parent.exists() and not any(parent.iterdir()):
        parent.rmdir()


def sweep_tmp_partial(run_dir: Path) -> None:
    work = work_dir(run_dir)
    tmp = work / "tmp"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True, exist_ok=True)
    staging = work / "artifact-staging"
    if staging.exists():
        for path in staging.glob("*.partial"):
            path.unlink(missing_ok=True)
    evidence = evidence_dir(run_dir)
    for path in evidence.rglob("*.tmp"):
        path.unlink(missing_ok=True)


def append_recovery_suffix(
    run_dir: Path,
    ledger: RunLedger,
    *,
    source: ProcessSource | None = None,
    signaller: Signaller | None = None,
) -> None:
    run_id = str(ledger.records[0].get("run_id", run_dir.name))
    # B2-C11: the run's processes are dealt with before anything is finalized
    _record_group_stop(ledger, run_id, source=source, signaller=signaller)
    sweep_tmp_partial(run_dir)
    # B2-C11: the lane is folded before anything is finalized and before `interrupted`; a run
    # with no lane (or one already folded before the restart) writes no row. The recovery error
    # below stays the class's own (a restart is the supervisor's cause, like a cancel).
    fold.fold_into_ledger(run_dir, ledger, None)
    # MC-15: a run finalized here has an explanation; the one it already wrote (before the restart)
    # stands, and none is made up for a run that ends by any other means
    if ledger.terminal_state() is None and not ledger.has_kind("error_record"):
        ledger.append(
            "error_record",
            run_id=run_id,
            code=codes.EXECUTION_INTERRUPTED,
            phase="recovery",
            message="the service restarted while the run was in flight",
        )
    result_state = _result_state(run_dir)
    completeness = "complete" if result_state == "complete" else "partial"

    if not ledger.has_kind("evidence_finalized"):
        ledger.append(
            "evidence_finalized",
            run_id=run_id,
            completeness=completeness,
            result_state=result_state,
        )

    if ledger.terminal_state() is None:
        ledger.append("interrupted", run_id=run_id)

    fsync_dir(evidence_dir(run_dir))
    rematerialize_meta(run_dir, ledger)


def _record_group_stop(
    ledger: RunLedger,
    run_id: str,
    *,
    source: ProcessSource | None,
    signaller: Signaller | None,
) -> None:
    """One `group_stop` for a run that was started and has none: take B2-C11's branch from the
    run's identity rows. A run that never started spawned nothing and has no process-group
    target. A run that already has its row (the conductor died after it) is not stopped twice."""
    if not ledger.has_kind("started") or ledger.has_kind("group_stop"):
        return
    decision = procident.recovery_decision(ledger.records, source)
    if decision.branch == "iii":

        def record(ident: procident.Identity) -> None:
            ledger.append("process_identity", run_id=run_id, **ident.fields())

        stop = procident.stop_recovered(decision, record=record, source=source, signaller=signaller)
        confirmed = stop.confirmed_gone
    else:
        confirmed = bool(decision.confirmed_gone)
    ledger.append("group_stop", run_id=run_id, confirmed_gone=confirmed, method=decision.method)


def rematerialize_meta(run_dir: Path, ledger: RunLedger) -> None:
    evidence = evidence_dir(run_dir)
    run_id = str(ledger.records[0].get("run_id", run_dir.name))
    terminal = ledger.terminal_state() or "interrupted"
    ended = ledger.last_kind("execution_ended")
    duration_ms: int | None = None
    if ended is not None:
        raw = ended.get("duration_ms")
        if isinstance(raw, int):
            duration_ms = raw

    limit_record = ledger.last_kind("limit_exceeded")
    limits_exceeded: list[dict[str, object]] | None = None
    if limit_record is not None:
        raw = limit_record.get("markers")
        if isinstance(raw, list):
            limits_exceeded = [item for item in raw if isinstance(item, dict)]

    artifact_count = sum(
        1 for record in ledger.records if record.get("kind") == "artifact_available"
    )
    meta: dict[str, object] = {
        "run_id": run_id,
        "classification": terminal,
        "duration_ms": duration_ms,
        "result_state": _result_state(run_dir),
        "artifact_count": artifact_count,
        "limits_exceeded": limits_exceeded,
        "recovered": True,
    }
    error = ledger.last_kind("error_record")
    if error is not None:  # a copy of the ledger row's fields, the row being the authority
        meta["error"] = {key: error.get(key) for key in ("code", "phase", "message")}
    atomic_write_json(evidence / "meta.json", meta)
    fsync_dir(evidence)


def _result_state(run_dir: Path) -> str:
    evidence = evidence_dir(run_dir)
    if (evidence / "result.state").exists():
        return "too_large"
    result_path = evidence / "result.json"
    index_path = evidence / "result.index"
    if result_path.exists() and index_path.exists():
        try:
            json.loads(result_path.read_text(encoding="utf-8"))
            return "complete"
        except json.JSONDecodeError:
            return "invalid"
    if result_path.exists():
        return "invalid"
    return "absent"


def find_run_dir(home: Path, run_id: str) -> Path | None:
    runs_root = home / "runs"
    if not runs_root.exists():
        return None
    for month_dir in runs_root.iterdir():
        candidate = month_dir / run_id
        if candidate.is_dir() and ledger_path(candidate).exists():
            return candidate
    return None


def seed_interrupted_run(home: Path, run_id: str, *, last_kind: str = "started") -> Path:
    """Test helper — create a run dir stopped mid-flight."""
    run_dir = run_dir_for(home, run_id, month="2099-01")
    evidence = evidence_dir(run_dir)
    work = work_dir(run_dir)
    evidence.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    (work / "tmp").mkdir(parents=True, exist_ok=True)
    ledger = RunLedger.open(ledger_path(run_dir))
    ledger.append("created", run_id=run_id, spec_hash="test", plugin="echo", version="0.1.0")
    if last_kind in {"admitted", "started", "execution_ended", "evidence_finalized"}:
        ledger.append("admitted", run_id=run_id, snapshot_id="snap_test")
    if last_kind in {"started", "execution_ended", "evidence_finalized"}:
        ledger.append("started", run_id=run_id)
    if last_kind == "execution_ended":
        ledger.append(
            "execution_ended",
            run_id=run_id,
            classification="failed",
            exit_code=1,
            duration_ms=1,
        )
    if last_kind == "evidence_finalized":
        ledger.append(
            "execution_ended",
            run_id=run_id,
            classification="failed",
            exit_code=1,
            duration_ms=1,
        )
        ledger.append(
            "evidence_finalized",
            run_id=run_id,
            completeness="partial",
            result_state="absent",
        )
    return run_dir
