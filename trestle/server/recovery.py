"""Crash recovery from ledger authority (R-STORE-18, R-STORE-19)."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from trestle.common import codes
from trestle.common.fsutil import atomic_write_json, fsync_dir
from trestle.common.plan.compiler import AdmittedPlan
from trestle.common.plan.formats import PlanInvalid, UnknownPlanFormat
from trestle.server import answer, fold, procident, sweep
from trestle.server.config import OperatorLimits, load_config
from trestle.server.ledger import (
    TERMINAL_KINDS,
    RunLedger,
    evidence_dir,
    ledger_path,
    run_dir_for,
    work_dir,
)
from trestle.server.procident import ProcessSource, Signaller
from trestle.server.runstate import refresh_state

_MID_EXECUTION_KINDS = frozenset(
    {
        "progress",
        "artifact_declared",
        "artifact_available",
        "limit_exceeded",
    }
)


def recover_run_dir(
    run_dir: Path,
    *,
    source: ProcessSource | None = None,
    signaller: Signaller | None = None,
    limits: OperatorLimits | None = None,
    sweep_io: sweep.SweepIO | None = None,
    promote: bool = False,
) -> None:
    """Recover one run directory (B2-C11): the reaper's takeover of a dead owner's run, which it
    reaches only while holding the run's owner lock (v0.3.1 rule 3). `limits` and `sweep_io` are
    test seams for the release sweep (the operator's limits, the command runner and the clock); a
    caller passing neither (the reaper, the d2 driver) gets the configured limits and real
    commands. `promote`: the run had no secret values, so its outputs are promoted as the owner
    would have (rule 3), after the process stop and before the staged files are swept."""
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
            append_recovery_suffix(
                run_dir,
                ledger,
                source=source,
                signaller=signaller,
                limits=limits,
                sweep_io=sweep_io,
                promote=promote,
            )
        return

    if last_kind == "execution_ended":
        append_recovery_suffix(
            run_dir,
            ledger,
            source=source,
            signaller=signaller,
            limits=limits,
            sweep_io=sweep_io,
            promote=promote,
        )
        return

    if last_kind in {"created", "admitted", "started"} or last_kind in _MID_EXECUTION_KINDS:
        append_recovery_suffix(
            run_dir,
            ledger,
            source=source,
            signaller=signaller,
            limits=limits,
            sweep_io=sweep_io,
            promote=promote,
        )
        return

    append_recovery_suffix(
        run_dir,
        ledger,
        source=source,
        signaller=signaller,
        limits=limits,
        sweep_io=sweep_io,
        promote=promote,
    )


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
    limits: OperatorLimits | None = None,
    sweep_io: sweep.SweepIO | None = None,
    promote: bool = False,
) -> None:
    run_id = str(ledger.records[0].get("run_id", run_dir.name))
    # B2-C11: the run's processes are dealt with before anything is finalized
    group_confirmed = _record_group_stop(ledger, run_id, source=source, signaller=signaller)
    if promote:
        _promote_reaped(run_dir, ledger, run_id)
    sweep_tmp_partial(run_dir)
    # B2-C11: the lane is folded, and swept in plan release rank, before anything is finalized and
    # before `interrupted`; a run with no lane (or one already folded before the restart) writes
    # no row. The recovery error below stays the class's own (a restart is the supervisor's cause,
    # like a cancel).
    plan, plan_known = _plan_of(run_dir)
    folded = fold.fold_into_ledger(run_dir, ledger, plan)
    if group_confirmed is not None:  # a run that never started spawned nothing: no target
        _sweep_after_restart(
            run_dir,
            ledger,
            run_id,
            folded,
            group_confirmed,
            plan,
            plan_known,
            limits=limits,
            sweep_io=sweep_io,
        )
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
        # B4 Ordering (B2-C11): the answer is projected from the durable inputs, `recovered`,
        # before the terminal row
        spec = _read_spec(run_dir)
        answer.write_finalized(
            run_dir, answer.answer_for_run(run_dir, ledger.records, "interrupted", spec)
        )
        answer.write_child_views(run_dir, spec)  # V-1.3: each child view, terminal form
        ledger.append("interrupted", run_id=run_id)

    fsync_dir(evidence_dir(run_dir))
    rematerialize_meta(run_dir, ledger)


def _promote_reaped(run_dir: Path, ledger: RunLedger, run_id: str) -> None:
    """v0.3.1 rule 3: a reaped run with no secret values keeps what it left, as artifacts. With no
    secrets the owner's scrub (`redact.Scrubber` over the run's roots) writes the same bytes in any
    process; names the dead owner already promoted are skipped."""
    from trestle.common import redact
    from trestle.server.conductor import promote_outputs

    home = run_dir.parents[2]
    markers: list[dict[str, object]] = []
    scrubber = redact.Scrubber(roots=redact.run_roots(run_dir, home))
    promote_outputs(run_dir, ledger, run_id, scrubber, markers, skip_available=True)
    if markers:
        ledger.append("limit_exceeded", run_id=run_id, markers=markers)


def _read_spec(run_dir: Path) -> dict[str, object]:
    try:
        loaded = json.loads((evidence_dir(run_dir) / "spec.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _plan_of(run_dir: Path) -> tuple[AdmittedPlan | None, bool]:
    """The run's admitted plan and whether its format is one this reader knows: a spec with no
    plan is the implicit depth-1 plan (`None`, known, B2-C1); a plan of another format, or one
    that does not verify, is unknown (`None`, unknown): recovery finalizes such a run interrupted
    with its cleanup unknown and releases nothing."""
    try:
        return fold.plan_of_spec(_read_spec(run_dir)), True
    except (UnknownPlanFormat, PlanInvalid):
        return None, False


def _sweep_after_restart(
    run_dir: Path,
    ledger: RunLedger,
    run_id: str,
    folded: fold.FoldedRecord,
    group_confirmed: bool,
    plan: AdmittedPlan | None,
    plan_known: bool,
    *,
    limits: OperatorLimits | None,
    sweep_io: sweep.SweepIO | None,
) -> None:
    """B2-C11: sweep the folded lane with the recovery's `GroupStop`, in plan release rank; an
    unconfirmed target is never `nothing_created` here (B2-C9). A plan of an unknown format is
    never swept and never released: one `sweep_skipped` row makes its cleanup unknown. A run whose
    sweep already wrote its rows (the conductor, or an earlier recovery) is not swept twice."""
    if ledger.has_kind(sweep.SWEEP_DISPOSITION_KIND) or ledger.has_kind(sweep.SWEEP_SKIPPED_KIND):
        return
    if not plan_known:
        ledger.append(
            sweep.SWEEP_SKIPPED_KIND, run_id=run_id, reason=sweep.SKIPPED_UNKNOWN_PLAN_FORMAT
        )
        return
    effective = limits or load_config(run_dir.parents[2]).operator_limits
    result = sweep.sweep_detailed(
        folded,
        _RecoveredGroup(group_confirmed),
        sweep.budget_for(effective),
        effective,
        plan,
        recovery=True,
        io=sweep_io,
    )
    sweep.write_rows(ledger, run_id, result)


@dataclass(frozen=True)
class _RecoveredGroup:
    confirmed_gone: bool


def _record_group_stop(
    ledger: RunLedger,
    run_id: str,
    *,
    source: ProcessSource | None,
    signaller: Signaller | None,
) -> bool | None:
    """One `group_stop` for a run that was started and has none: take B2-C11's branch from the
    run's identity rows. A run that never started spawned nothing and has no process-group
    target (None). A run that already has its row (the conductor died after it) is not stopped
    twice: its recorded `confirmed_gone` stands. Returns whether the group is confirmed gone."""
    if not ledger.has_kind("started"):
        return None
    existing = ledger.last_kind("group_stop")
    if existing is not None:
        return existing.get("confirmed_gone") is True
    # v0.3.1 Problem C: the identities are in the ledger (the leader's) and the sidecar, and the
    # recovery's own rows go to the sidecar
    sidecar = procident.Sidecar(procident.sidecar_path(ledger.path.parent), run_id)
    decision = procident.recovery_decision(ledger.records, source, sidecar=sidecar.records())
    src = source if source is not None else procident.SYSTEM
    recorded: list[procident.Identity] = []
    if decision.branch == "iii":

        def record(ident: procident.Identity) -> None:
            sidecar.append(ident)
            recorded.append(ident)

        stop = procident.stop_recovered(decision, record=record, source=source, signaller=signaller)
        confirmed = stop.confirmed_gone
    else:
        confirmed = bool(decision.confirmed_gone)
    if not ledger.has_kind("process_summary"):
        idents = [*decision.identities, *recorded]
        # branch (i) has nothing recorded, and reads no process table
        alive = 0 if confirmed or not idents else procident.count_alive(idents, src)
        ledger.append("process_summary", run_id=run_id, seen=len(idents), alive=alive)
    ledger.append("group_stop", run_id=run_id, confirmed_gone=confirmed, method=decision.method)
    return confirmed


def rematerialize_meta(run_dir: Path, ledger: RunLedger) -> None:
    refresh_state(run_dir, ledger)  # v0.3.1 Problem C: state.json follows the ledger's last row
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
    """Test helper — create a run dir stopped mid-flight: a run with no owner (its `created` row
    names none, as a v0.3.0 run's) and its live marker, so the reaper finalizes it."""
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
    from trestle.server.home import write_marker

    write_marker(home, run_id, {"owner": None, "month": "2099-01", "lease_key": None})
    return run_dir
