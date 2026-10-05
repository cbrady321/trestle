"""L.SV-1.3: U2 folds the lane into B2's `FoldedRecord` at finalization and at recovery (before
`interrupted`); a run with no lane folds empty (B2-C12); a failing step feeds the error record
(MC-15, DM-02)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from tests.core.spine import support
from tests.proof import records
from tests.single.record import support as sup
from tests.single.record.test_attempt_lane import applied, issue, make_lane, ticket
from trestle.child.attempt_lane import TicketRefusal
from trestle.common import codes
from trestle.common import lane_format as lf
from trestle.common.fsutil import append_ndjson
from trestle.common.types import RunView
from trestle.query.fs import FilesystemQueryBackend
from trestle.server import fold
from trestle.server.ledger import RunLedger, ledger_path
from trestle.server.recovery import recover_run_dir, seed_interrupted_run

LANE_UNAVAILABLE = "execution.lane_unavailable"


@dataclass(frozen=True)
class Accepted:
    """The two fields of `PlanAccepted` the fold reads."""

    selected_scope: frozenset[tuple[str, ...]]
    lane_entries: int


def lane_of(run_dir: Path) -> lf.LaneRead:
    return lf.read_lane(lf.lane_path(run_dir))


def plant(run_dir: Path, *, scope: Any = ((),), entries: int = 64) -> Any:
    """An AttemptLane over `run_dir` with its plan recorded (the lane file is created)."""
    return make_lane(run_dir.parent, entries=entries, scope=scope, run_id=run_dir.name)


def failing_step(code: str = "unit_raised", *path: str) -> lf.StepEntry:
    return sup.step_entry(*path, kind=lf.StepKind.FAILED, code=code)


def drive(plugin: str, args: dict[str, Any], plant_lane: Any = None) -> tuple[Any, Path]:
    """Admit `plugin`, plant a lane in its run directory, then run it to its terminal row."""
    kernel = support.spine_kernel()
    order = support.admit_order(kernel, plugin, args)
    run_dir = support.run_dir_of(kernel, order.run_id)
    if plant_lane is not None:
        plant_lane(run_dir)
    kernel.control.conductor.drive(order)
    return kernel, run_dir


def folded_rows(run_dir: Path) -> list[dict[str, Any]]:
    return [r for r in records.ledger_rows(run_dir).rows if r.get("kind") == "lane_folded"]


def lane_summary(read: lf.LaneRead) -> list[tuple[int, str, str | None]]:
    return [
        (
            e.seq,
            lf.entry_class(e),
            None if isinstance(e, lf.PlanEntry) else lf.encode_path(e.lineage.path),
        )
        for e in read.entries
    ]


def _plant_full(run_dir: Path) -> None:
    lane = plant(run_dir)
    made = ticket(issue(lane, effect="up", release=sup.ARGV))
    lane.confirm(made, applied("sel-1"))
    lane.record_step(sup.step_entry(kind=lf.StepKind.NO_ACTION, code="unit_no_action"))
    lane.record_end(sup.node_end())


# ------------------------------------------------------------------ finalization and recovery


def test_fold_at_finalization_equals_lane() -> None:
    _, run_dir = drive("echo", {"message": "hi"}, _plant_full)
    assert records.node_record(run_dir).terminal == "succeeded"
    rows = folded_rows(run_dir)
    read = lane_of(run_dir)
    assert [(r["lane_seq"], r["entry_class"], r["path"]) for r in rows] == lane_summary(read)
    (issue_row,) = [r for r in rows if r["entry_class"] == "issue"]
    assert issue_row["descriptor"] == lf.descriptor_record(sup.ARGV)  # V-10 form, as a row
    assert all(r["descriptor"] is None for r in rows if r["entry_class"] != "issue")
    kinds = support.kinds(run_dir)
    last_folded = max(i for i, k in enumerate(kinds) if k == "lane_folded")
    assert kinds.index("group_stop") < kinds.index("lane_folded")
    assert last_folded < kinds.index("execution_ended") < kinds.index("evidence_finalized")
    assert "error_record" not in kinds  # a lane does not turn a success into an error
    folded = fold.fold_lane(run_dir, None)
    assert folded.root == run_dir.name and folded.ended and not folded.overflowed
    assert [t.effect for t in folded.entries] == ["up"] and folded.unconfirmed == ()
    assert len(folded.ends) == 1


@pytest.mark.proves(
    "WR-CANCEL-3", "WR-CANCEL-3:fold-before-interrupted", "A", "single", "PROC", "CI"
)
def test_fold_at_recovery_precedes_interrupted(tmp_path: Path) -> None:
    run_dir = seed_interrupted_run(tmp_path / "home", "r_fold_recovery")
    _plant_full(run_dir)
    recover_run_dir(run_dir)
    kinds = support.kinds(run_dir)
    assert kinds.count("lane_folded") == len(lane_of(run_dir).entries) > 0
    assert max(i for i, k in enumerate(kinds) if k == "lane_folded") < kinds.index("interrupted")
    assert kinds.index("group_stop") < kinds.index("lane_folded")
    rows = folded_rows(run_dir)
    assert [(r["lane_seq"], r["entry_class"], r["path"]) for r in rows] == lane_summary(
        lane_of(run_dir)
    )
    # the interrupted run's explanation is still the restart's own (a supervisor cause)
    (error,) = support.rows_of(run_dir, "error_record")
    assert error["code"] == codes.EXECUTION_INTERRUPTED
    again = support.kinds(run_dir)
    recover_run_dir(run_dir)  # a second pass finds the terminal row and adds nothing
    assert support.kinds(run_dir) == again


def test_fold_into_ledger_folds_a_lane_once(tmp_path: Path) -> None:
    run_dir = seed_interrupted_run(tmp_path / "home", "r_fold_once")
    _plant_full(run_dir)
    ledger = RunLedger.open(ledger_path(run_dir))
    fold.fold_into_ledger(run_dir, ledger, None)
    written = len(folded_rows(run_dir))
    fold.fold_into_ledger(run_dir, ledger, None)  # a restart after finalization
    assert len(folded_rows(run_dir)) == written == len(lane_of(run_dir).entries)


# ------------------------------------------------------------------ the fold itself


def test_first_node_end_kept_later_dropped(tmp_path: Path) -> None:
    run_dir = tmp_path / "r_two_ends"
    lane = plant(run_dir)
    lane.record_end(sup.node_end(condition=lf.Condition.FAILED, code="first"))
    read = lane_of(run_dir)
    second = sup.node_end(condition=lf.Condition.BLOCKED, code="second")
    # planted past the lane's own DUPLICATE refusal, as a corrupted or foreign writer would
    append_ndjson(lf.lane_path(run_dir), lf.encode_record(second, len(read.entries) + 1))
    folded = fold.fold_lane(run_dir, None)
    assert [e.code for e in folded.ends] == ["first"]  # a later one for the vertex is dropped
    assert len(lane_of(run_dir).entries) == 3  # plan + two ends: nothing was lost from the file


def test_ended_iff_node_end_for_every_vertex(tmp_path: Path) -> None:
    scope = frozenset({("a",), ("b",)})
    accepted = Accepted(scope, 64)
    run_dir = tmp_path / "r_ended"
    lane = plant(run_dir, scope=tuple(scope))
    assert not fold.fold_lane(run_dir, accepted).ended  # no NodeEnd yet
    lane.record_end(sup.node_end("a"))
    assert not fold.fold_lane(run_dir, accepted).ended  # one vertex ended, one not
    lane.record_end(sup.node_end("b"))
    folded = fold.fold_lane(run_dir, accepted)
    assert folded.ended and {e.lineage.path for e in folded.ends} == scope
    # a run with ends but no plan entry never ended normally (V-4.8: record_plan comes first)
    bare = tmp_path / "r_no_plan"
    append_ndjson(lf.lane_path(bare), lf.encode_record(sup.node_end("a"), 1))
    assert not fold.fold_lane(bare, Accepted(frozenset({("a",)}), 64)).ended


def test_ended_walks_only_the_selected_alternatives(tmp_path: Path) -> None:
    scope = frozenset({(), ("c",), ("c", "x"), ("c", "y")})
    accepted = Accepted(scope, 64)
    run_dir = tmp_path / "r_selected"
    lane = make_lane(tmp_path, scope=tuple(scope), run_id="r_selected", plan=False)
    lane.record_plan(lf.PlanIdentity("d", "a", {("c",): ("c", "x")}, "o"))
    for path in ((), ("c",)):
        lane.record_end(sup.node_end(*path))
    assert not fold.fold_lane(run_dir, accepted).ended
    lane.record_end(sup.node_end("c", "x"))  # y was not selected: it owes no NodeEnd
    assert fold.fold_lane(run_dir, accepted).ended


def test_no_lane_run_folds_empty(tmp_path: Path) -> None:
    run_dir = seed_interrupted_run(tmp_path / "home", "r_no_lane")
    ledger = RunLedger.open(ledger_path(run_dir))
    ledger.append("stop_row", run_id="r_no_lane", cause="cancel", lane_committed_length=0)
    folded = fold.fold_lane(run_dir, None)
    assert folded == fold.FoldedRecord(
        root="r_no_lane",
        stop_rows=(fold.StopRow("cancel", 0, ledger.records[-1]["at"]),),
        plan=None,
        entries=(),
        steps=(),
        ends=(),
        ended=False,
        unconfirmed=(),
        unknown_paths=(),
        overflowed=False,  # never 'lane unreadable' (B2-C12, B4-C1)
        refused_full=False,
    )
    assert not fold.cleanup_is_unknown(folded)
    before = support.kinds(run_dir)
    fold.fold_into_ledger(run_dir, ledger, None)
    assert support.kinds(run_dir) == before  # no lane, no lane_folded row


def test_non_vertex_path_unknown_paths_cleanup_unknown(tmp_path: Path) -> None:
    run_dir = tmp_path / "r_unknown"
    lane = plant(run_dir)
    lane.record_step(failing_step("unit_raised", "ghost", "deeper"))
    lane.record_end(sup.node_end("ghost"))  # a NodeEnd for a non-vertex is unknown too
    lane.record_end(sup.node_end())
    folded = fold.fold_lane(run_dir, None)
    assert folded.unknown_paths == ("ghost/deeper", "ghost")
    assert folded.steps == () and [e.lineage.path for e in folded.ends] == [()]  # excluded
    assert fold.cleanup_is_unknown(folded) and not folded.overflowed
    assert folded.ended  # the root has its end; unknown entries do not make it un-ended


def test_torn_entry_sets_overflowed_cleanup_unknown(tmp_path: Path) -> None:
    run_dir = tmp_path / "r_torn"
    lane = plant(run_dir)
    lane.record_end(sup.node_end())
    assert not fold.cleanup_is_unknown(fold.fold_lane(run_dir, None))
    with lf.lane_path(run_dir).open("ab") as fh:
        fh.write(b'{"class":"issue","seq":3,"path":"sv')  # a torn committed-looking tail
    folded = fold.fold_lane(run_dir, None)
    assert folded.overflowed and fold.cleanup_is_unknown(folded)  # never clean
    assert folded.ended and len(folded.ends) == 1  # what was committed is still read
    # an undecodable committed entry is the same
    other = tmp_path / "r_undecodable"
    plant(other)
    append_ndjson(lf.lane_path(other), {"class": "issue", "seq": 9, "path": "svc"})
    assert fold.fold_lane(other, None).overflowed


def test_full_lane_folds_refused_full_not_overflowed(tmp_path: Path) -> None:
    """Lane full to capacity, the refused node's NodeEnd carrying LANE_UNAVAILABLE."""
    run_dir = tmp_path / "r_full"
    lane = plant(run_dir, entries=7)
    made = ticket(issue(lane, effect="up"))
    assert issue(lane, effect="up") is TicketRefusal.LANE_UNAVAILABLE  # fewer than 3 free
    lane.confirm(made, applied("s"))
    end = sup.node_end(condition=lf.Condition.BLOCKED, code=LANE_UNAVAILABLE, provenance=None)
    assert lane.record_end(end) is None
    accepted = Accepted(frozenset({()}), 7)
    folded = fold.fold_lane(run_dir, accepted)
    assert folded.refused_full and not folded.overflowed
    assert not fold.cleanup_is_unknown(folded)  # refused_full alone changes no cleanup (B2-C9)
    # the same lane whose NodeEnd carries another code, or with room to spare, is not refused_full
    roomy = tmp_path / "r_roomy"
    room = plant(roomy, entries=64)
    room.record_end(end)
    assert not fold.fold_lane(roomy, Accepted(frozenset({()}), 64)).refused_full
    other = tmp_path / "r_full_other"
    small = plant(other, entries=7)
    ticket(issue(small, effect="up"))
    small.record_end(sup.node_end(code="unit_raised", condition=lf.Condition.FAILED))
    assert not fold.fold_lane(other, Accepted(frozenset({()}), 7)).refused_full


def test_unconfirmed_lists_every_issued_never_confirmed_entry(tmp_path: Path) -> None:
    run_dir = tmp_path / "r_unconfirmed"
    lane = plant(run_dir)
    ticket(issue(lane, effect="up"))
    second = ticket(issue(lane, effect="up"))
    lane.confirm(second, lf.Confirmation(lf.ConfirmationStatus.UNKNOWN, None, None))
    third = ticket(issue(lane, effect="up"))
    lane.confirm(third, lf.Confirmation(lf.ConfirmationStatus.NOT_APPLIED, "refused", None))
    folded = fold.fold_lane(run_dir, None)
    assert [t.attempt for t in folded.unconfirmed] == [1, 2]  # None and UNKNOWN, not NOT_APPLIED
    assert [t.attempt for t in folded.entries] == [1, 2, 3]


# ------------------------------------------------------------------ the error record


def _error_row(run_dir: Path) -> dict[str, Any]:
    (row,) = support.rows_of(run_dir, "error_record")
    return row


def _meta_error(run_dir: Path) -> Any:
    meta = json.loads((run_dir / "evidence" / "meta.json").read_text(encoding="utf-8"))
    return meta.get("error")


@pytest.mark.proves(
    "WR-EVID-1", "WR-EVID-1:lane-step-folded-to-error-record", "A", "single", "PROC+MCP", "CI"
)
def test_failing_step_code_reaches_error_record_runview_last_error_meta() -> None:
    def plant_failed(run_dir: Path) -> None:
        lane = plant(run_dir)
        lane.record_step(failing_step("unit_raised"))
        lane.record_end(sup.node_end(condition=lf.Condition.FAILED, code="unit_raised"))

    kernel, run_dir = drive("exiter", {"status": 3}, plant_failed)
    row = _error_row(run_dir)
    assert records.node_record(run_dir).terminal == "worker_exit"
    # one code, from the lane, before the classification's own worker_exit
    assert row["code"] == "unit_raised" and row["code"] != codes.EXECUTION_WORKER_EXIT
    assert row["phase"] == "root" and "unit_raised" in row["message"]
    view = kernel.control.project.status(run_dir.name)
    assert isinstance(view, RunView) and view.error is not None
    assert view.error["code"] == row["code"]
    assert _meta_error(run_dir)["code"] == row["code"]
    last = FilesystemQueryBackend(kernel.home).query("last_error", {"run_id": run_dir.name})
    assert isinstance(last, dict) and last["items"][0]["message"] == row["message"]
    # after a restart the same code: meta.json is rebuilt from the ledger, no second row
    (run_dir / "evidence" / "meta.json").unlink()
    recover_run_dir(run_dir)
    assert _meta_error(run_dir)["code"] == "unit_raised"
    assert len(support.rows_of(run_dir, "error_record")) == 1


def test_child_error_outranks_step_entry() -> None:
    def plant_failed(run_dir: Path) -> None:
        lane = plant(run_dir)
        lane.record_step(failing_step("unit_raised"))

    _, run_dir = drive("raiser", {}, plant_failed)
    row = _error_row(run_dir)
    assert row["code"] == codes.EXECUTION_PLUGIN_RAISED  # child_error.json first (MC-15)
    assert len(support.rows_of(run_dir, "error_record")) == 1  # exactly one (SA-13)


def test_held_failing_step_error_record_from_node_end() -> None:
    def plant_held(run_dir: Path) -> None:
        lane = plant(run_dir)  # no failing step: the loop held it in process (V-4.5)
        lane.record_end(
            sup.node_end(
                condition=lf.Condition.BLOCKED,
                code="effect_unconfirmed",
                human_action="Check the service, then resend.",
                resend=lf.Resend.SUCCEEDS_AFTER_ACTION,
            )
        )

    _, run_dir = drive("exiter", {"status": 3}, plant_held)
    row = _error_row(run_dir)
    assert row["code"] == "effect_unconfirmed"
    assert "Check the service" in row["message"]


def test_lane_error_source_order_and_release_steps_are_not_conditions(tmp_path: Path) -> None:
    run_dir = tmp_path / "r_error_order"
    lane = plant(run_dir)
    release = sup.step_entry(code="release_failed", with_handle=sup.handle())
    lane.record_step(release)  # a release outcome: never the node's condition (V-3.7)
    lane.record_end(sup.node_end(condition=lf.Condition.SATISFIED))
    assert fold.lane_error(fold.fold_lane(run_dir, None)) is None
    lane.record_step(failing_step("first_failure"))
    lane.record_step(sup.step_entry(kind=lf.StepKind.NO_ACTION, code="unit_no_action"))
    error = fold.lane_error(fold.fold_lane(run_dir, None))
    assert error is not None and error["code"] == "first_failure"  # a NoAction is no failure
    lane.record_step(failing_step("last_failure"))
    later = fold.lane_error(fold.fold_lane(run_dir, None))
    assert later is not None and later["code"] == "last_failure"  # the terminal one
