"""L.TR-L.2: L.TR-3.2's barrier and upstream-coverage proofs re-run through the host.

The same claims as `tests/tree/test_tr3_barrier.py`, but the tree is admitted by
`ControlSurface.run(completion="terminal")` (MC-B3-02: never MC-26) and checked on the finalized
run's lane (MC-19), so the walk that orders the tree is the one a caller gets: a real wrapper and
child process behind the kernel. Every claim is read from record order, never from timing: a leaf
"runs" from its first lane entry to its `NodeEnd`, and the barrier is a fact about which entry
precedes which. The behaviour fixtures are `barrier_run` (four branches under a bound of two, and
a `join` that needs them all) and `upstream_covered` (a child precondition the producer it needs
covers)."""

from __future__ import annotations

from typing import Any

import pytest

from tests.proof import records
from tests.tree import hostpath
from tests.tree import treekit as tk
from tests.tree.test_tr1_admission import run_dirs, source
from trestle.common.types import PublishView, RunView
from trestle.server.main import Kernel

proves_clause = pytest.mark.proves("WR-PLAN-10", "A4.2", "A", "tree", "PROC", "CI")
proves_peak = pytest.mark.proves(
    "WR-PLAN-10", "WR-PLAN-10:peak-within-bound", "A", "tree", "PROC", "CI"
)
proves_barrier = pytest.mark.proves(
    "WR-PLAN-10", "WR-PLAN-10:barrier-order", "A", "tree", "PROC", "CI"
)
proves_verify = pytest.mark.proves(
    "WR-VERIFY-2", "WR-VERIFY-2:dependent-after-postcondition-pass", "A", "tree", "PROC", "CI"
)
proves_covered = pytest.mark.proves(
    "WR-PLAN-12", "WR-PLAN-12:upstream-covered", "A", "tree", "PROC", "CI"
)

BRANCHES = ["a", "b", "c", "d"]
BOUND = 2  # `barrier_run`'s declared concurrency
ENV = "dev"  # the environment both fixtures name: their root's, so the tree's one lease key


def run_finalized(kernel: Kernel, fixture: str) -> tuple[RunView, list[dict[str, Any]]]:
    """Publish `fixture` and run it to its terminal answer through the host; the view the caller
    got and the finalized run's lane entries in record order."""
    published = hostpath.publish_tree_via_host(kernel, source(fixture))
    assert isinstance(published, PublishView), published
    view = hostpath.run_tree_via_host(kernel, published.name, {"env": ENV})
    assert isinstance(view, RunView), view
    assert view.state == "succeeded", view
    (run_dir,) = run_dirs(kernel)
    assert run_dir.name == view.run_id
    lane = records.lane_rows(run_dir)
    assert not lane.problems and not lane.torn, lane.problems
    return view, [row.entry for row in lane.rows]


def first_index(rows: list[dict[str, Any]], path: str) -> int:
    return next(n for n, row in enumerate(rows) if row.get("path") == path)


def end_index(rows: list[dict[str, Any]], path: str) -> int:
    return next(
        n for n, row in enumerate(rows) if row.get("path") == path and row["class"] == "end"
    )


def condition_of(rows: list[dict[str, Any]], path: str) -> Any:
    return rows[end_index(rows, path)]["condition"]


@proves_clause
@proves_peak
@proves_barrier
@proves_verify
def test_host_barrier_order_and_bound(tree_kernel: Kernel) -> None:
    """`barrier_run` through `ControlSurface.run`: at the declared bound of two the branches
    overlap two at a time and never three (each waits in its create for a partner, so the bound is
    reached; the lane shows it is not exceeded), and `join` (which needs all four) writes its first
    record after every branch's postcondition-pass `NodeEnd`."""
    view, rows = run_finalized(tree_kernel, "barrier_run")
    answer = view.to_dict()["answer"]
    assert answer["outcome"] == "passed", answer
    assert tk.peak_running(rows, BRANCHES) == BOUND
    ends = {row["path"]: row for row in rows if row["class"] == "end"}
    assert set(ends) == {"", *BRANCHES, "join"}
    for path in (*BRANCHES, "join"):  # a composite root ends with no condition of its own
        assert condition_of(rows, path) == "satisfied", path
    first_join = first_index(rows, "join")
    for branch in BRANCHES:
        assert end_index(rows, branch) < first_join, f"join started before {branch} was terminal"


@proves_clause
@proves_covered
@proves_verify
def test_host_upstream_covered_precondition_runs(tree_kernel: Kernel) -> None:
    """`upstream_covered` (L.TR-1.5's tree with the `needs` that covers the precondition) through
    the host: the consumer runs, and its first record follows the producer's postcondition-pass
    `NodeEnd` (the producer lingers before it acts, so a consumer started early would show)."""
    view, rows = run_finalized(tree_kernel, "upstream_covered")
    assert view.to_dict()["answer"]["outcome"] == "passed"
    assert condition_of(rows, "producer") == "satisfied"
    assert end_index(rows, "producer") < first_index(rows, "consumer")
    assert condition_of(rows, "consumer") == "satisfied"
