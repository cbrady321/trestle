"""U2's fold of the attempt lane into B2's `FoldedRecord` (L.SV-1.3; MC-19, MC-15).

The fold runs at finalization and at recovery (before `interrupted`, B2-C11). It is the only host
module that imports the lane codec (`trestle.common.lane_format`); `answer.py` and the sweep read
the lane only through `fold_lane` (L.SV-4.2, L.SV-3.7). It is a pure read of the durable record;
`fold_into_ledger` is the one function that writes, and it writes exactly one `lane_folded`
ledger row per committed lane entry, before `execution_ended`.

A run with no lane file (a plain plugin, or a run finalized while queued) folds to the empty
`FoldedRecord` of B2-C12 and writes no row, so every ledger a run without a lane produced is
unchanged (SA-15, d2).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from trestle.common import lane_format as lf
from trestle.common.errtext import sanitize
from trestle.common.plan import bounds
from trestle.server.ledger import RunLedger, ledger_path

# The ledger kind U2's `StopRow` (B2-C15) is written under; L.SV-3.6 writes it, the fold reads it.
STOP_ROW_KIND = "stop_row"
LANE_FOLDED_KIND = "lane_folded"

# The `NodeEnd.code` that means the lane refused the node (V-11 `LANE_UNAVAILABLE`, F-11(a)). V-11's
# name is provisional and the wire spelling is L.SV-4.2's (`execution.lane_unavailable`); both
# spellings count until that leaf makes it one constant.
LANE_UNAVAILABLE_CODES = frozenset({"execution.lane_unavailable", "lane_unavailable"})

_ROOT: tuple[str, ...] = ()


class AcceptedPlan(Protocol):
    """The two fields of B2's `PlanAccepted` the fold reads (AM-16: the type itself is created by
    L.SV-3.4, after this leaf). `selected_scope` holds node paths, as `tuple[str, ...]` or any
    object with `.segments`."""

    @property
    def selected_scope(self) -> Iterable[Any]: ...

    @property
    def lane_entries(self) -> int: ...


@dataclass(frozen=True, slots=True)
class StopRow:
    """B2 `StopRow`, as U2's ledger row carries it (B2-C15)."""

    cause: str
    lane_committed_length: int | None
    at: str


@dataclass(frozen=True, slots=True)
class FoldedRecord:
    """B2's `FoldedRecord`, field for field."""

    root: str  # the run whose directory held the lane, never a lane field (B2-I3)
    stop_rows: tuple[StopRow, ...]
    plan: lf.PlanIdentity | None
    entries: tuple[lf.TicketEntry, ...]
    steps: tuple[lf.StepEntry, ...]
    ends: tuple[lf.NodeEnd, ...]  # the first NodeEnd per vertex, in lane order (B2-C7)
    ended: bool  # a NodeEnd for every vertex of V_run (V-4.8)
    unconfirmed: tuple[lf.TicketEntry, ...]  # issued and never confirmed (None or UNKNOWN)
    unknown_paths: tuple[str, ...]  # entry paths (NodeEnds included) that are not vertices
    overflowed: bool  # an entry was lost or torn, or the lane was unreadable as a whole
    refused_full: bool  # issue was refused because the lane was full; nothing was lost


def _path_of(node: Any) -> tuple[str, ...]:
    segments = getattr(node, "segments", node)
    return tuple(segments)


def _scope(accepted: AcceptedPlan | None) -> frozenset[tuple[str, ...]]:
    """`selected_scope`, or the implicit one-vertex plan's root for a plan-less root (B2-C1)."""
    if accepted is None:
        return frozenset({_ROOT})
    return frozenset(_path_of(n) for n in accepted.selected_scope)


def _lane_entries(accepted: AcceptedPlan | None, scope: frozenset[tuple[str, ...]]) -> int:
    if accepted is not None:
        return int(accepted.lane_entries)
    return bounds.LANE_BASE_ENTRIES + len(scope) + 2  # V-13 LANE_ENTRIES


def _stop_rows(run_dir: Path) -> tuple[StopRow, ...]:
    path = ledger_path(run_dir)
    if not path.exists():
        return ()
    rows: list[StopRow] = []
    for record in RunLedger.open(path).records:
        if record.get("kind") != STOP_ROW_KIND:
            continue
        length = record.get("lane_committed_length")
        rows.append(
            StopRow(
                cause=str(record.get("cause", "")),
                lane_committed_length=length if isinstance(length, int) else None,
                at=str(record.get("at", "")),
            )
        )
    return tuple(rows)


def _empty(run_dir: Path, *, overflowed: bool = False) -> FoldedRecord:
    return FoldedRecord(
        root=run_dir.name,
        stop_rows=_stop_rows(run_dir),
        plan=None,
        entries=(),
        steps=(),
        ends=(),
        ended=False,
        unconfirmed=(),
        unknown_paths=(),
        overflowed=overflowed,
        refused_full=False,
    )


def _reserved(read_entries: tuple[lf.Entry, ...], v_run: frozenset[tuple[str, ...]]) -> int:
    """The slots B2-C7's reservation holds in the durable lane: the plan slot until the plan is
    recorded, one per vertex of `V_run` without its NodeEnd, and the follow-up slots each ticket
    reserved (two per ticket) that its confirmation and result-or-release have not used."""
    has_plan = any(isinstance(e, lf.PlanEntry) for e in read_entries)
    ended = {e.lineage.path for e in read_entries if isinstance(e, lf.NodeEnd)}
    open_slots = 2 * sum(1 for e in read_entries if isinstance(e, lf.IssueEntry))
    for e in read_entries:
        if isinstance(e, (lf.ConfirmationEntry, lf.ResultEntry, lf.ReleasedEntry)):
            open_slots -= 1
    open_ends = len(v_run - ended) if has_plan else 0
    return (0 if has_plan else 1) + open_ends + max(0, open_slots)


def _fold(run_dir: Path, accepted: AcceptedPlan | None) -> tuple[FoldedRecord, lf.LaneRead | None]:
    lane = lf.lane_path(run_dir)
    if not lane.exists():
        return _empty(run_dir), None  # B2-C12: never 'lane unreadable'
    try:
        read = lf.read_lane(lane)
    except OSError:
        return _empty(run_dir, overflowed=True), None  # unreadable as a whole
    scope = _scope(accepted)
    plan = next((e.plan for e in read.entries if isinstance(e, lf.PlanEntry)), None)
    non_plan = [e for e in read.entries if not isinstance(e, lf.PlanEntry)]

    unknown: list[str] = []
    seen: set[tuple[str, ...]] = set()
    for e in non_plan:
        if e.lineage.path not in scope and e.lineage.path not in seen:
            seen.add(e.lineage.path)
            unknown.append(lf.encode_path(e.lineage.path))
    in_scope = [e for e in non_plan if e.lineage.path in scope]

    tickets = lf.assemble_tickets(in_scope)
    steps = tuple(e for e in in_scope if isinstance(e, lf.StepEntry))
    ends: dict[tuple[str, ...], lf.NodeEnd] = {}
    for e in in_scope:
        if isinstance(e, lf.NodeEnd):
            ends.setdefault(e.lineage.path, e)  # the first per vertex stands (B2-C7)

    v_run = lf.walked_set(scope, plan.selection if plan is not None else {})
    ended = plan is not None and v_run <= set(ends)

    capacity = _lane_entries(accepted, scope)
    free = capacity - (len(read.entries) + read.unknown) - _reserved(read.entries, v_run)
    refused_full = free < 3 and any(e.code in LANE_UNAVAILABLE_CODES for e in ends.values())

    folded = FoldedRecord(
        root=run_dir.name,
        stop_rows=_stop_rows(run_dir),
        plan=plan,
        entries=tickets,
        steps=steps,
        ends=tuple(ends.values()),
        ended=ended,
        unconfirmed=tuple(
            t
            for t in tickets
            if t.confirmation is None or t.confirmation.status == lf.ConfirmationStatus.UNKNOWN
        ),
        unknown_paths=tuple(unknown),
        overflowed=read.torn,
        refused_full=refused_full,
    )
    return folded, read


def fold_lane(run_dir: Path, accepted: AcceptedPlan | None) -> FoldedRecord:
    """B2's `FoldedRecord` for `run_dir` (B2-C7, B2-C12). `accepted` is `spec.plan`'s
    `PlanAccepted`, or None before L.SV-3.4 and for a plan-less root (the implicit one-vertex
    plan). Pure: reads the ledger's stop rows and the lane, writes nothing."""
    return _fold(run_dir, accepted)[0]


def fold_into_ledger(
    run_dir: Path, ledger: RunLedger, accepted: AcceptedPlan | None
) -> FoldedRecord:
    """The fold, plus one `lane_folded` row per committed lane entry (lineage key, class,
    descriptor) appended to `ledger` in lane order. Called at finalization before the terminal
    rows and at recovery before `interrupted`. A run with no lane file writes no row; a ledger
    that already holds `lane_folded` rows (a restart after finalization) gets none again."""
    folded, read = _fold(run_dir, accepted)
    if read is None or ledger.has_kind(LANE_FOLDED_KIND):
        return folded
    run_id = str(ledger.records[0].get("run_id", run_dir.name)) if ledger.records else run_dir.name
    for entry in read.entries:
        ledger.append(
            LANE_FOLDED_KIND,
            run_id=run_id,
            lane_seq=entry.seq,
            path=None if isinstance(entry, lf.PlanEntry) else lf.encode_path(entry.lineage.path),
            entry_class=lf.entry_class(entry),
            descriptor=(
                lf.descriptor_record(entry.release) if isinstance(entry, lf.IssueEntry) else None
            ),
        )
    return folded


def cleanup_is_unknown(folded: FoldedRecord) -> bool:
    """B2-C9's lane inputs to cleanup: an overflowed lane, or an entry outside the admitted plan,
    makes cleanup `unknown`, never clean. A full lane (`refused_full`) alone changes nothing."""
    return folded.overflowed or bool(folded.unknown_paths)


def lane_error(folded: FoldedRecord) -> dict[str, str] | None:
    """The lane's two sources for the run's one `error_record` (MC-15, DM-02), after
    `child_error.json` and before the classification: the root vertex's failing terminal
    `StepEntry` (its code, phase = its path), else the code its `NodeEnd` carries when the failing
    step was held in process (V-4.5). A step returned by `release` (a handle is set) is a cleanup
    outcome, never a condition (V-3.7). One vertex in A-1: the root; A-2 widens the choice."""
    failing = [
        s
        for s in folded.steps
        if s.lineage.path == _ROOT
        and s.handle is None
        and s.kind in (lf.StepKind.FAILED, lf.StepKind.BLOCKED)
    ]
    if failing:
        step = failing[-1]
        return _composed(step.code, step.kind.value, step.human_action)
    end = next((e for e in folded.ends if e.lineage.path == _ROOT), None)
    if end is not None and end.code is not None and end.condition != lf.Condition.SATISFIED:
        kind = end.condition.value if end.condition is not None else "ended"
        return _composed(end.code, kind, end.human_action)
    return None


def _composed(code: str, kind: str, human_action: str | None) -> dict[str, str]:
    message = f"the root node ended in a {kind} step ({code})"
    if human_action:
        message = f"{message}: {human_action}"
    return {"code": code, "phase": "root", "message": sanitize(message, {})}
