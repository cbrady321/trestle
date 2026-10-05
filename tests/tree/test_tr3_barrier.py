"""L.TR-3.2: the loop walks an `AllDeclaration` with the `needs` barrier and the declared bound.

Every claim is read from record order in the run's real lane (MC-19), never from timing: a leaf
"runs" from its first lane entry to its `NodeEnd`, and the barrier is a fact about which entry
precedes which. The tree is run in-library through the loop (MC-26's rig: the real child services
and attempt lane under a manual clock); no tree is admissible before TR-L."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest

from tests.proof import tolerances
from tests.tree import treekit as tk
from trestle.workflow.units import Failed

proves_peak = pytest.mark.proves(
    "WR-PLAN-10", "WR-PLAN-10:peak-within-bound", "A", "tree", "PROC", "CI"
)
proves_barrier = pytest.mark.proves(
    "WR-PLAN-10", "WR-PLAN-10:barrier-order", "A", "tree", "PROC", "CI"
)
proves_verify = pytest.mark.proves(
    "WR-VERIFY-2", "WR-VERIFY-2:dependent-after-postcondition-pass", "A", "tree", "PROC", "CI"
)
proves_membership = pytest.mark.proves(
    "WR-PLAN-1", "WR-PLAN-1:parallel-membership", "A", "tree", "LOGIC", "CI"
)

LEAVES = ["l0", "l1", "l2", "l3", "l4", "l5"]


def _first(rows: list[dict[str, Any]], path: str) -> int:
    return next(n for n, row in enumerate(rows) if row.get("path") == path)


def _end(rows: list[dict[str, Any]], path: str) -> int:
    return next(
        n for n, row in enumerate(rows) if row.get("path") == path and row["class"] == "end"
    )


@proves_peak
@pytest.mark.parametrize("bound", [1, 2, 3])
def test_peak_concurrency_within_bound(tmp_path: Path, bound: int) -> None:
    """Six independent leaves under a declared bound: the bound is reached (every leaf waits, in
    its create, for `bound` leaves to be inside at once, so a walk that ran fewer would break the
    barrier) and never exceeded, as the lane records show."""
    meeting = threading.Barrier(bound, timeout=tolerances.JOIN_WAIT_S)
    inside = 0
    peak_inside = 0
    guard = threading.Lock()

    def arrive(path: str) -> None:
        nonlocal inside, peak_inside
        with guard:
            inside += 1
            peak_inside = max(peak_inside, inside)
        meeting.wait()
        with guard:
            inside -= 1

    root = tk.group("app", tuple(tk.bind(n) for n in LEAVES), concurrency=bound)
    rig = tk.tree_rig(tmp_path, root, {n: tk.leaf_unit(n) for n in LEAVES}, tk.PathMarker(arrive))
    rig.run()
    rows = rig.rows()
    assert tk.peak_running(rows, LEAVES) == bound
    assert peak_inside == bound
    ends = rig.ends()
    assert {p for p in ends if p} == set(LEAVES)
    assert all(ends[p]["condition"] == "satisfied" for p in LEAVES)


@proves_barrier
@proves_verify
def test_post_barrier_start_after_every_branch_terminal(tmp_path: Path) -> None:
    """`join` needs both branches. `left` is held open until the test lets it go; `right` ends at
    once. While `left` is open `join` must not have started; afterwards its first record follows
    both branches' postcondition-pass `NodeEnd`s."""
    release_left = threading.Event()
    left_open = threading.Event()

    def hold(path: str) -> None:
        if path == "left":
            left_open.set()
            assert tk.wait_for(release_left)

    root = tk.group(
        "app", (tk.bind("left"), tk.bind("right"), tk.bind("join", "left", "right")), concurrency=3
    )
    units = {n: tk.leaf_unit(n) for n in ("left", "right", "join")}
    rig = tk.tree_rig(tmp_path, root, units, tk.PathMarker(hold))
    runner = threading.Thread(target=rig.run)
    runner.start()
    try:
        assert tk.wait_for(left_open)
        threading.Event().wait(tolerances.SETTLE_S)  # long enough for a wrong start to show
        assert "join" not in rig.marker.paths("create"), "join started before left was terminal"
    finally:
        release_left.set()
        runner.join(tolerances.JOIN_WAIT_S)
    assert not runner.is_alive()
    rows = rig.rows()
    for branch in ("left", "right"):
        end = rows[_end(rows, branch)]
        assert end["condition"] == "satisfied"  # the postcondition-pass record
        assert _end(rows, branch) < _first(rows, "join")


@proves_verify
def test_dependent_start_after_postcondition_pass(tmp_path: Path) -> None:
    """`web` needs `db`: it starts after `db`'s postcondition-pass record and never without one.
    With a `db` that ends without a pass, `web` writes no record but its own `NodeEnd`
    (`NOT_STARTED`, no ticket)."""
    root = tk.group("app", (tk.bind("db"), tk.bind("web", "db")))
    passing = tk.tree_rig(tmp_path / "pass", root, {n: tk.leaf_unit(n) for n in ("db", "web")})
    passing.run()
    rows = passing.rows()
    assert rows[_end(rows, "db")]["condition"] == "satisfied"
    assert _end(rows, "db") < _first(rows, "web")

    def broken(unit: Any, params: Any, state: Any, effects: Any, ctx: Any) -> Failed:
        return Failed("db.broke", "no pass")

    failing = tk.tree_rig(
        tmp_path / "fail",
        root,
        {"db": tk.leaf_unit("db", advance=broken), "web": tk.leaf_unit("web")},
    )
    failing.run()
    web = [row for row in failing.rows() if row.get("path") == "web"]
    assert [row["class"] for row in web] == ["end"]
    assert (web[0]["condition"], web[0]["cut"]) == (None, "not_started")
    assert "web" not in failing.marker.paths("create")


@proves_membership
def test_membership_invariant_under_parallelism(tmp_path: Path) -> None:
    """The same plan run at concurrency 1, 2 and 3: the same vertices are ended, each with the
    same condition, code and cut, and the host's answer (its outcome and primary) is the same."""
    seen: list[tuple[dict[str, tuple[Any, ...]], Any, Any]] = []
    for bound in (1, 2, 3):
        root = tk.group(
            "app",
            (
                tk.bind("a"),
                tk.bind("b"),
                tk.bind("c", "a"),
                tk.bind("d", "a", "b"),
                tk.bind("e"),
            ),
            concurrency=bound,
        )
        units = {n: tk.leaf_unit(n) for n in "abcde"}
        rig = tk.tree_rig(tmp_path / f"b{bound}", root, units)
        rig.run()
        got = tk.answer_of(rig)
        ends = {
            path: (end["condition"], end["code"], end["cut"]) for path, end in rig.ends().items()
        }
        seen.append((ends, got.outcome, got.primary))
    first = seen[0]
    assert set(first[0]) == {"", "a", "b", "c", "d", "e"}
    assert all(item == first for item in seen[1:])


@pytest.mark.proves("WR-PLAN-12", "WR-PLAN-12:upstream-covered", "A", "tree", "LOGIC", "CI")
def test_upstream_covered_child_runs_after_postcondition_record(tmp_path: Path) -> None:
    """L.TR-1.5's fixture, its child precondition covered by a `needs` on the producer whose
    postcondition it is: the consumer runs, and its first record follows the producer's
    postcondition-pass `NodeEnd` (held: the producer is open until the test lets it go, so a
    consumer started early would show)."""
    entry = tk.fixture_entry(
        "uncovered_precondition",
        {
            'ChildBinding(unit="consumer", params={}, needs=())': (
                'ChildBinding(unit="consumer", params={}, needs=("producer",))'
            )
        },
    )
    release_producer = threading.Event()
    producer_open = threading.Event()

    def hold(path: str) -> None:
        if path == "producer":
            producer_open.set()
            assert tk.wait_for(release_producer)

    rig = tk.rig_of_entry(tmp_path, entry, tk.PathMarker(hold))
    runner = threading.Thread(target=rig.run)
    runner.start()
    try:
        assert tk.wait_for(producer_open)
        threading.Event().wait(tolerances.SETTLE_S)
        assert "consumer" not in rig.marker.paths("create")
    finally:
        release_producer.set()
        runner.join(tolerances.JOIN_WAIT_S)
    assert not runner.is_alive()
    rows = rig.rows()
    assert rows[_end(rows, "producer")]["condition"] == "satisfied"
    assert _end(rows, "producer") < _first(rows, "consumer")
    assert rig.ends()["consumer"]["condition"] == "satisfied"
