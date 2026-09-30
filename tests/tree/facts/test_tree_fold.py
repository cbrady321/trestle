"""L.TR-2.1: the fold keys the lane by lineage over every vertex of `V_run` (V-4.8), at
finalization and at recovery alike.

The writer is the test acting as the loop through MC-19's `AttemptLane` surface (TR-2 preamble,
A2c6-2): the loop cannot walk a composite before `L.TR-3.2`, so every run directory here is one
`tests.tree.runs` planted."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.core.spine import support
from tests.fixtures.trees import generators
from tests.proof import harness, records
from tests.single.record import support as sup
from tests.tree import runs
from trestle.common import lane_format as lf
from trestle.common.plan import bounds
from trestle.server import fold
from trestle.server.ledger import RunLedger, ledger_path
from trestle.server.main import Kernel
from trestle.server.recovery import recover_run_dir

proves_slices = pytest.mark.proves(
    "WR-UNIT-2", "WR-UNIT-2:record-slices", "A", "tree", "LOGIC", "CI"
)
proves_integrity = pytest.mark.proves(
    "C-RECORD-INTEGRITY", "C-RECORD-INTEGRITY:per-root", "A", "tree", "LOGIC", "CI"
)

THREE_LEVEL = "three_level"


def _admit(kernel: Kernel, name: str = THREE_LEVEL) -> runs.TreeRun:
    return runs.admit(kernel, generators.fixture_tree(name))


def _folded(run: runs.TreeRun) -> fold.FoldedRecord:
    return fold.fold_lane(run.run_dir, run.plan)


def _rows(run_dir: Path) -> list[tuple[Any, ...]]:
    return [
        (r["lane_seq"], r["entry_class"], r["path"], r["descriptor"])
        for r in records.ledger_rows(run_dir).rows
        if r.get("kind") == "lane_folded"
    ]


# ---- own pin (depth 1, before the edit)


def test_depth1_fold_rows_golden(tmp_path: Path) -> None:
    """The depth-1 fold rows and record, byte for byte (`preserve`): recorded before this leaf's
    edit and unchanged by it."""
    from tests.single.record.test_attempt_lane import applied, issue, make_lane, ticket

    kernel = support.spine_kernel()
    order = support.admit_order(kernel, "echo", {"message": "hi"})
    run_dir = support.run_dir_of(kernel, order.run_id)
    lane = make_lane(run_dir.parent, entries=64, scope=((),), run_id=run_dir.name)
    made = ticket(issue(lane, effect="up", release=sup.ARGV))
    lane.confirm(made, applied("sel-1"))
    lane.record_step(sup.step_entry(kind=lf.StepKind.NO_ACTION, code="unit_no_action"))
    lane.record_end(sup.node_end())
    kernel.control.conductor.drive(order)
    assert _rows(run_dir) == [
        (1, "plan", None, None),
        (2, "issue", "", lf.descriptor_record(sup.ARGV)),
        (3, "confirmation", "", None),
        (4, "step", "", None),
        (5, "end", "", None),
    ]
    folded = fold.fold_lane(run_dir, None)
    assert [lf.encode_path(e.lineage.path) for e in folded.ends] == [""]
    assert folded.ended and not folded.overflowed and not folded.refused_full
    assert folded.unknown_paths == () and folded.unconfirmed == ()
    assert [(t.effect, t.attempt) for t in folded.entries] == [("up", 1)]


# ---------------------------------------------------------------- one NodeEnd per vertex


@proves_slices
def test_fold_one_nodeend_per_vertex(tree_kernel: Kernel) -> None:
    run = _admit(tree_kernel)
    lane = runs.lane_of(run)
    runs.write_all_ends(run, lane)
    folded = _folded(run)
    vertices = set(run.paths())
    assert len(vertices) == 5  # app, data, db, cache, web: read from the plan, not a constant
    assert sorted(e.lineage.path for e in folded.ends) == sorted(vertices)
    assert len(folded.ends) == len(vertices)
    assert folded.ended and not folded.overflowed and folded.unknown_paths == ()
    # a composite that ended normally carries no condition; each leaf is satisfied (B1-C11)
    by_path = {e.lineage.path: e for e in folded.ends}
    assert by_path[()].condition is None and by_path[("data",)].condition is None
    assert by_path[("data", "db")].condition == lf.Condition.SATISFIED

    # withhold one leaf's NodeEnd: V-4.8 `ended` is false
    other = _admit(tree_kernel)
    partial = runs.lane_of(other)
    withheld = other.leaves()[0]
    for path in other.paths():
        if path == withheld:
            continue
        leaf = path in set(other.leaves())
        partial.record_end(runs.end(other, path) if leaf else runs.end(other, path, None))
    short = _folded(other)
    assert not short.ended
    assert withheld not in {e.lineage.path for e in short.ends}
    assert len(short.ends) == len(vertices) - 1


@proves_slices
def test_fold_non_vertex_path_to_unknown_paths_planted(tree_kernel: Kernel) -> None:
    run = _admit(tree_kernel)
    lane = runs.lane_of(run)
    runs.write_all_ends(run, lane)
    # a lane is written under the admitted scope; a foreign entry can only arrive by a planted
    # line (the lane refuses it), appended past the committed length in the lane's own framing
    foreign = sup.step_entry("data", "ghost", kind=lf.StepKind.NO_ACTION, code="unit_no_action")
    from trestle.common.fsutil import append_ndjson

    append_ndjson(lf.lane_path(run.run_dir), lf.encode_record(foreign, seq=lane_seq(run) + 1))
    folded = _folded(run)
    assert folded.unknown_paths == ("data/ghost",)
    assert ("data", "ghost") not in {e.lineage.path for e in folded.ends}
    assert all(s.lineage.path != ("data", "ghost") for s in folded.steps)  # in no vertex
    assert fold.cleanup_is_unknown(folded)  # never read as clean (B4-I7)
    assert folded.ended  # the planted line adds no vertex and removes none


def lane_seq(run: runs.TreeRun) -> int:
    return len(lf.read_lane(lf.lane_path(run.run_dir)).entries)


@proves_integrity
def test_fold_second_nodeend_refused_first_kept(tree_kernel: Kernel) -> None:
    run = _admit(tree_kernel)
    lane = runs.lane_of(run)
    target = ("data", "db")
    first = runs.end(run, target, lf.Condition.BLOCKED, code="unit.first")
    second = runs.end(run, target, lf.Condition.SATISFIED)
    assert lane.record_end(first) is None
    assert lane.record_end(second) == lf.LaneRefusal.DUPLICATE  # the lane refuses (B2-C7)
    assert [
        e.lineage.path
        for e in lf.read_lane(lf.lane_path(run.run_dir)).entries
        if isinstance(e, lf.NodeEnd)
    ] == [target]
    # and were a second one on disk (a torn writer, a foreign appender) the fold keeps the first
    from trestle.common.fsutil import append_ndjson

    append_ndjson(lf.lane_path(run.run_dir), lf.encode_record(second, seq=lane_seq(run) + 1))
    folded = _folded(run)
    (kept,) = [e for e in folded.ends if e.lineage.path == target]
    assert kept.condition == lf.Condition.BLOCKED and kept.code == "unit.first"


@proves_slices
def test_fold_equals_lane_multi_path(tree_kernel: Kernel) -> None:
    run = _admit(tree_kernel)
    lane = runs.lane_of(run)
    from tests.single.record.test_attempt_lane import applied, issue, ticket

    for leaf in run.leaves()[:3]:
        made = ticket(issue(lane, *leaf, effect="up", release=sup.ARGV))
        lane.confirm(made, applied(f"sel-{'-'.join(leaf)}"))
        lane.record_step(sup.step_entry(*leaf, kind=lf.StepKind.NO_ACTION, code="unit_no_action"))
    runs.write_all_ends(run, lane)
    folded = _folded(run)
    read = lf.read_lane(lf.lane_path(run.run_dir))
    on_disk = [e for e in read.entries if not isinstance(e, lf.PlanEntry)]
    assert len({e.lineage.path for e in on_disk}) >= 3  # 3+ paths (MC-10)
    assert {(t.lineage.path, t.effect) for t in folded.entries} == {
        (e.lineage.path, e.effect) for e in on_disk if isinstance(e, lf.IssueEntry)
    }
    assert {s.lineage.path for s in folded.steps} == {
        e.lineage.path for e in on_disk if isinstance(e, lf.StepEntry)
    }
    assert [e.lineage.path for e in folded.ends] == [
        e.lineage.path for e in on_disk if isinstance(e, lf.NodeEnd)
    ]
    assert folded.unconfirmed == () and folded.unknown_paths == () and folded.ended


# ---------------------------------------------------------------- finalization equals recovery


@proves_integrity
def test_recovery_fold_equals_finalization_fold(tree_kernel: Kernel) -> None:
    finalized = _admit(tree_kernel)
    recovered = _admit(tree_kernel)
    for run in (finalized, recovered):
        lane = runs.lane_of(run)
        from tests.single.record.test_attempt_lane import applied, issue, ticket

        leaf = run.leaves()[0]
        made = ticket(issue(lane, *leaf, effect="up", release=sup.ARGV))
        lane.confirm(made, applied("sel-1"))
        runs.write_all_ends(run, lane)

    harness.drive_tree(finalized.admitted)
    RunLedger.open(ledger_path(recovered.run_dir)).append("started", run_id=recovered.run_id)
    recover_run_dir(recovered.run_dir)

    assert _rows(recovered.run_dir) == _rows(finalized.run_dir) != []
    a, b = _folded(finalized), _folded(recovered)
    assert [(e.lineage.path, e.condition) for e in a.ends] == [
        (e.lineage.path, e.condition) for e in b.ends
    ]
    assert (a.ended, a.overflowed, a.refused_full, a.unknown_paths) == (
        b.ended,
        b.overflowed,
        b.refused_full,
        b.unknown_paths,
    )
    assert [(t.lineage.path, t.effect) for t in a.entries] == [
        (t.lineage.path, t.effect) for t in b.entries
    ]
    kinds = support.kinds(recovered.run_dir)
    assert max(i for i, k in enumerate(kinds) if k == "lane_folded") < kinds.index("interrupted")


# ---------------------------------------------------------------- a full lane (MC-19)


@proves_slices
def test_fold_full_lane_condition_from_nodeend(tree_kernel: Kernel) -> None:
    run = _admit(tree_kernel)
    lane = runs.lane_of(run)
    leaf = run.leaves()[0]
    held: lf.StepEntry | None = None
    filler = sup.step_entry(kind=lf.StepKind.NO_ACTION, code="unit_no_action")
    attempts = 0
    while True:  # fill every unreserved slot with steps until the lane is FULL (V-4.5)
        step = sup.step_entry(
            *leaf, kind=lf.StepKind.BLOCKED, code="unit.blocked", human_action="Free port 80."
        )
        refusal = lane.record_step(step)
        attempts += 1
        if refusal is not None:
            assert refusal == lf.LaneRefusal.FULL
            held = step  # the loop keeps the refused step in process
            break
        assert attempts <= run.plan.lane_entries, "the lane never filled"
    assert held is not None and filler is not None
    for path in run.paths():
        if path == leaf:
            refusal = lane.record_end(
                runs.end(
                    run,
                    path,
                    lf.Condition.BLOCKED,
                    code="unit.blocked",
                    human_action="Free port 80.",
                    provenance=None,
                )
            )
        else:
            refusal = lane.record_end(
                runs.end(run, path, None if path not in run.leaves() else lf.Condition.SATISFIED)
            )
        assert refusal is None, (path, refusal)  # a reserved slot is never FULL
    folded = _folded(run)
    assert folded.ended and not folded.overflowed
    assert not fold.cleanup_is_unknown(folded)
    (end,) = [e for e in folded.ends if e.lineage.path == leaf]
    assert (end.condition, end.code, end.human_action) == (
        lf.Condition.BLOCKED,
        "unit.blocked",
        "Free port 80.",
    )


@proves_slices
def test_hundred_vertex_nodeends_into_reserved_slots_no_overflow(tree_kernel: Kernel) -> None:
    tree = generators.hundred_node(100)
    run = runs.admit(tree_kernel, tree)
    vertices = len(run.plan.vertices)  # V read from the plan, never a literal
    assert vertices == tree.vertices > 100
    assert run.plan.lane_entries == bounds.LANE_BASE_ENTRIES + vertices + 2  # V-13 LANE_ENTRIES
    lane = runs.lane_of(run)
    action = "x" * bounds.HUMAN_ACTION_MAX
    assert bounds.text_bytes(action) == bounds.HUMAN_ACTION_MAX
    leaf = run.leaves()[0]
    while (
        lane.record_step(sup.step_entry(*leaf, kind=lf.StepKind.NO_ACTION, code="unit_no_action"))
        is None
    ):
        pass  # every unreserved slot is used
    for path in run.paths():
        if path == ():
            end = runs.end(run, path, None, provenance=None)
        else:
            end = runs.end(
                run,
                path,
                lf.Condition.BLOCKED,
                code="unit.blocked",
                human_action=action,
                provenance=None,
            )
        assert lane.record_end(end) is None, path  # into the reserved slot
    folded = _folded(run)
    assert len(folded.ends) == vertices
    assert folded.ended and not folded.overflowed
    root = next(e for e in folded.ends if e.lineage.path == ())
    assert root.condition is None
    assert all(e.human_action == action for e in folded.ends if e.lineage.path != ())
    assert not fold.cleanup_is_unknown(folded)
