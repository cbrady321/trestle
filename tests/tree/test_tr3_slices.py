"""L.TR-3.3: cooperative carved slices and timed-out subtrees.

A cooperating leaf past its carved slice is ended by the loop (`StepEntry(FAILED, CARVE_EXCEEDED)`
and a `NodeEnd` carrying that condition at its path, B1-O8); a composite whose own slice ends while
a leaf below it is still open gets `NodeEnd(FAILED, CARVE_EXCEEDED)` and every leaf below it is
stopped: from that verdict the subtree records only release starts, including a node two parents
share when either parent's subtree timed out (F-13(a)), and whatever its leaves created stays in
the root's ownership record and is released with the root's. In-library over the real lane and
services under a manual clock (MC-26); the host halves are `L.TR-L.5`'s."""

from __future__ import annotations

import threading
import time
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from trestle_packs.fakes import FakeMarker

from tests.core.spine import support
from tests.proof import ancestry, harness, tolerances
from tests.single.workflow import loopkit as kit
from tests.tree import treekit as tk
from trestle.common import clock
from trestle.common.outcome import OutcomeClass
from trestle.server.main import Kernel
from trestle.workflow import codes
from trestle.workflow.ports import ResourceCreate, ResourceOwned, ResourceReads
from trestle.workflow.values import Goal, NodePath, StopCause

proves_coop = pytest.mark.proves(
    "WR-UNIT-3", "WR-UNIT-3:coop-child-stopped-at-slice", "A", "tree", "LOGIC+PROC", "BOTH"
)
proves_nostart = pytest.mark.proves(
    "WR-UNIT-3", "WR-UNIT-3:no-start-after-slice-verdict", "A", "tree", "LOGIC+PROC", "BOTH"
)
proves_resources = pytest.mark.proves(
    "WR-UNIT-3", "WR-UNIT-3:subtree-resources-in-root-record", "A", "tree", "LOGIC+PROC", "BOTH"
)
proves_shared = pytest.mark.proves(
    "WR-UNIT-3", "WR-UNIT-3:shared-node-in-timed-out-subtree", "A", "tree", "LOGIC+PROC", "BOTH"
)


def _index(rows: list[dict[str, Any]], **fields: Any) -> int:
    return next(n for n, row in enumerate(rows) if all(row.get(k) == v for k, v in fields.items()))


def _under(rows: list[dict[str, Any]], prefix: str) -> list[tuple[int, dict[str, Any]]]:
    return [
        (n, row)
        for n, row in enumerate(rows)
        if row.get("path") == prefix or str(row.get("path", "")).startswith(prefix + "/")
    ]


def _coop_rig(tmp_path: Path) -> tuple[tk.TreeRig, FakeMarker]:
    entry = tk.fixture_entry("slice_coop")
    marker = FakeMarker(tmp_path / "markers", "run")
    fakes = {ResourceReads: marker, ResourceCreate: marker, ResourceOwned: marker}
    rig = tk.rig_of_entry(
        tmp_path / "run", entry, port_impl=fakes, request={"env": "dev"}, keep_units=True
    )
    return rig, marker


@proves_coop
@proves_nostart
def test_coop_child_timed_out_names_path(tmp_path: Path) -> None:
    """On `slice_coop`, `slow` polls until its carved slice ends: its `NodeEnd` is `FAILED`
    `CARVE_EXCEEDED` at its own path, its class `TIMED_OUT` (B4-T2 row 7), and the answer is
    `TIMED_OUT` with `incomplete` the primary's path (nothing is STOPPED or UNENDED). It starts
    nothing after the verdict: only its release issues past it."""
    rig, marker = _coop_rig(tmp_path)
    try:
        rig.run()
    finally:
        marker.close()
    rows = rig.rows()
    ends = rig.ends()
    slow = ends["slow"]
    assert (slow["condition"], slow["code"], slow["cut"]) == (
        "failed",
        "execution.carve_exceeded",
        None,
    )
    assert ends["quick"]["condition"] == "satisfied"  # the sibling is not cut by it
    step = _index(rows, **{"class": "step", "path": "slow", "code": "execution.carve_exceeded"})
    assert step < _index(rows, **{"class": "end", "path": "slow"})
    later = [row for row in rows[step:] if row.get("path") == "slow" and row["class"] == "issue"]
    assert {row["effect"] for row in later} <= {"stop"}, later  # release starts only

    got = tk.answer_of(rig)
    assert got.outcome is OutcomeClass.TIMED_OUT
    assert got.primary.path == ("slow",) and got.primary.node_class.value == "timed_out"
    assert got.incomplete == ("slow",)


def _nested(tmp_path: Path, gate: threading.Event, held: threading.Event) -> tk.TreeRig:
    """`app` runs `data` (`db` holds, `cache` needs `db`) and `web`. `db` never leaves its wait
    until the test lets it, as a leaf that does not listen to its slice would not."""
    data = tk.group("data", (tk.bind("db"), tk.bind("cache", "db")), concurrency=2, budget_s=100)
    app = tk.group("app", (tk.bind("data"), tk.bind("web")), concurrency=3, budget_s=150)
    units: dict[str, object] = {
        "data": data,
        "db": tk.held_unit("db", gate, on_hold=lambda path: held.set()),
        "cache": tk.leaf_unit("cache"),
        "web": tk.leaf_unit("web"),
    }
    return tk.tree_rig(tmp_path, app, units)


def _wait_end(rig: tk.TreeRig, path: str) -> bool:
    """Whether `path`'s `NodeEnd` is recorded within the join wait. Read mid-run: a torn last line
    (an append in flight) is "not yet", never a failure."""

    def ended() -> bool:
        return any(r.cls == "end" and r.path == path for r in rig.rig.lane().rows)

    deadline = time.monotonic() + tolerances.JOIN_WAIT_S
    while not ended() and time.monotonic() < deadline:
        time.sleep(tolerances.POLL_FINE_S)
    return ended()


def _run_to_verdict(rig: tk.TreeRig, gate: threading.Event, held: threading.Event) -> None:
    """Run the tree; once `db` holds and `web` has ended, move the clock past `data`'s slice, wait
    for the composite's verdict record, then let `db` go. `web`'s carved slice ends with `data`'s,
    so the move must come after `web` has run: on a loaded host `db` can hold before `web` has
    created anything, and the move then cuts `web` at its own slice (`{'data/db'}` stopped)."""
    runner = threading.Thread(target=rig.run)
    runner.start()
    try:
        assert tk.wait_for(held)
        assert _wait_end(rig, "web"), "web never ended before the clock moved"
        ended = rig.rig.services.slice_end(NodePath(("data",))) + timedelta(seconds=1)
        rig.rig.clock.now = ended
        _wait_end(rig, "data")
    finally:
        gate.set()
        runner.join(tolerances.JOIN_WAIT_S)
    assert not runner.is_alive()


@proves_nostart
def test_subtree_only_release_after_verdict(tmp_path: Path) -> None:
    gate, held = threading.Event(), threading.Event()
    rig = _nested(tmp_path, gate, held)
    _run_to_verdict(rig, gate, held)
    rows, ends = rig.rows(), rig.ends()

    assert (ends["data"]["condition"], ends["data"]["code"]) == (
        "failed",
        "execution.carve_exceeded",
    )
    assert ends["data"]["cut"] is None
    assert ends["data/db"]["cut"] == "stopped"  # open when its subtree timed out
    assert (ends["data/cache"]["cut"], ends["data/cache"]["condition"]) == ("not_started", None)
    assert ends["web"]["condition"] == "satisfied"  # outside the subtree: untouched
    assert ends[""]["cut"] is None  # the parent of a timed-out subtree is not itself stopped

    verdict = _index(rows, **{"class": "end", "path": "data"})
    for n, row in _under(rows, "data"):
        if n > verdict and row["class"] == "issue":
            assert row["effect"] == "stop", row  # no non-release start after the verdict record
    assert "data/cache" not in rig.marker.paths("create")  # never started

    got = tk.answer_of(rig)
    assert got.outcome is OutcomeClass.TIMED_OUT
    assert got.incomplete == ("data", "db")  # the lowest-ordinal STOPPED vertex


@proves_resources
def test_subtree_resources_stay_in_root_record(tmp_path: Path) -> None:
    gate, held = threading.Event(), threading.Event()
    rig = _nested(tmp_path, gate, held)
    _run_to_verdict(rig, gate, held)
    rows = rig.rows()

    # what `db` made before its subtree timed out is in the root's own record ...
    created = [r for r in rows if r["class"] == "confirmation" and r["path"] == "data/db"]
    assert [r["status"] for r in created if r["effect"] == "up"] == ["applied"]
    # ... and stays there as the root's to give back: the release pass stops it, after every end
    assert set(rig.marker.paths("stop")) == {"data/db", "web"}
    released = [r for r in rows if r["class"] == "released" and r["path"] == "data/db"]
    assert len(released) == 1
    last_end = max(n for n, r in enumerate(rows) if r["class"] == "end")
    assert _index(rows, **{"class": "released", "path": "data/db"}) > last_end


DIAMOND_EDITS = {
    # `right` one level deeper (through `mid`), so its slice ends before `left`'s: the shared node
    # is under a timed-out parent while its other parent is still live
    'ChildBinding(unit="right", params={}, needs=()),': (
        'ChildBinding(unit="mid", params={}, needs=()),'
    ),
    '"right": group("right", (SHARED,), budget=100, concurrency=1),': (
        '"mid": group("mid", (ChildBinding(unit="right", params={}, needs=()),), budget=110),\n'
        '        "right": group("right", (SHARED,), budget=100, concurrency=1),'
    ),
}


@proves_shared
def test_shared_node_in_timed_out_subtree_release_only(tmp_path: Path) -> None:
    """`shared_diamond`, `right` nested one level deeper: `right`'s slice ends first. `shared`
    (named by both `left` and `right`, placed under `left`) is inside the timed-out subtree, so it
    records only release starts from `right`'s verdict, although `left`, its other parent, is
    live."""
    entry = tk.fixture_entry("shared_diamond", DIAMOND_EDITS)
    gate, held = threading.Event(), threading.Event()
    behaviour = {"shared": tk.held_unit("shared", gate, on_hold=lambda path: held.set())}
    rig = tk.rig_of_entry(tmp_path, entry, behaviour=behaviour)

    right = NodePath(("mid", "right"))
    left = NodePath(("left",))
    assert rig.rig.services.slice_end(right) < rig.rig.services.slice_end(left)
    runner = threading.Thread(target=rig.run)
    runner.start()
    try:
        assert tk.wait_for(held)
        between = rig.rig.services.slice_end(right) + timedelta(seconds=1)
        assert between < rig.rig.services.slice_end(left)  # `left` is not over
        rig.rig.clock.now = between
        deadline = time.monotonic() + tolerances.JOIN_WAIT_S
        while "mid/right" not in rig.ends() and time.monotonic() < deadline:
            time.sleep(tolerances.POLL_FINE_S)
    finally:
        gate.set()
        runner.join(tolerances.JOIN_WAIT_S)
    assert not runner.is_alive()
    rows, ends = rig.rows(), rig.ends()

    assert (ends["mid/right"]["condition"], ends["mid/right"]["code"]) == (
        "failed",
        "execution.carve_exceeded",
    )
    assert ends["left"]["condition"] is None and ends["left"]["cut"] is None  # left was live
    assert ends["left/shared"]["cut"] == "stopped"
    verdict = _index(rows, **{"class": "end", "path": "mid/right"})
    after = [r for n, r in enumerate(rows) if n > verdict and r.get("path") == "left/shared"]
    assert all(r["effect"] == "stop" for r in after if r["class"] == "issue"), after


def _ancestors(proc: ancestry.ProcInfo, table: dict[int, ancestry.ProcInfo]) -> list[int]:
    chain: list[int] = []
    current = proc
    while current.ppid in table and current.ppid not in chain and current.ppid > 1:
        chain.append(current.ppid)
        current = table[current.ppid]
    return chain


@pytest.mark.proves(
    "WR-UNIT-3", "WR-UNIT-3:coop-child-stopped-at-slice", "A", "tree", "LOGIC+PROC", "BOTH"
)
def test_noncoop_grandchild_fixture_publishes_and_grandchild_is_attributable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`slice_noncoop_grandchild` publishes and runs as a real run (a kernel admits it, a wrapper
    and a child process execute it, the tree walk runs on the child's threads): the process tree
    its non-cooperating leaf starts, a process ignoring SIGTERM and its own child, is attributable
    to the run (MC-13): each is a descendant of the run's own process and carries the run's tag.
    A cancel then ends the whole tree (`L.TR-L.5` measures the deadline form of the same)."""
    monkeypatch.setattr(clock, "grace", support.TEST_GRACE_S)
    monkeypatch.setattr(clock, "kill", support.TEST_KILL_S)
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    fixture = plugins / "slice_noncoop_grandchild.py"
    fixture.write_text(tk.fixture_source("slice_noncoop_grandchild"), encoding="utf-8")
    kernel: Kernel = harness.fresh_kernel([plugins], home=tmp_path / "home")
    tag = str(tmp_path / "noncoop-tree")
    assert len(tag) >= support.MIN_MARKER
    admitted = harness.admit_tree(
        fixture, {"tag": tag, "seconds": tolerances.JOIN_WAIT_S * 6}, kernel=kernel
    )
    with support.reaping(tag), support.reaping(admitted.run_id):
        conductor = threading.Thread(target=lambda: harness.drive_tree(admitted), daemon=True)
        conductor.start()
        assert support.wait_until(lambda: len(support.marked(tag)) >= 2, tolerances.JOIN_WAIT_S), (
            "the leaf's process tree never appeared"
        )
        table = {p.pid: p for p in ancestry.snapshot()}
        run_processes = {p.pid for p in support.marked(admitted.run_id)}
        assert run_processes, "no process carries the run's id"
        tagged = support.marked(tag)
        assert len(tagged) == 2, tagged  # the process the leaf started and its own child
        for proc in tagged:
            assert set(_ancestors(proc, table)) & run_processes, proc  # attributable to the run

        admitted.kernel.control.cancel(admitted.run_id)
        conductor.join(timeout=clock.stop_bound + tolerances.JOIN_WAIT_S)
        assert not conductor.is_alive(), "the run never reached its terminal row"
        assert support.wait_until(lambda: not support.marked(tag), tolerances.JOIN_WAIT_S)


# ---- P4: the root is carved nothing (its slice end is the release point) ----------------------

SOLO_DEADLINE_S = 50.0
SOLO_SLOW_OBSERVE_S = 15.0  # the first observation takes this long: the attempt starts late and
# the release point (40 s) comes before its max_wait ends (15 + 30 s)


def _solo_never_ready(clocks: list[kit.ManualClock]) -> kit.Unit:
    """A one-vertex root whose marker appears once created and is never ready; its declaration
    fits its budget and deadline, and its max_wait runs past the release point because its first
    observation is slow (`SOLO_SLOW_OBSERVE_S` on the clock `clocks` holds)."""
    decl = replace(
        kit.declaration(effects=kit.MARKER_EFFECTS, max_attempts=1, max_wait_s=30.0, budget_s=40.0),
        unit="solo",
    )
    base = tk.leaf_unit("solo", declaration=decl)

    def observe(unit: kit.Unit, params: Any, reads: Any, ctx: Any) -> Any:
        if len(clocks) == 1:
            clocks.pop().advance(SOLO_SLOW_OBSERVE_S)
        seen = reads.read(ResourceReads).observe(kit.SPEC, ctx.lineage, tk.EFFECT)
        return kit.observation(selector_present=seen.selector_present, ready=False)

    return kit.Unit(base.decl, observe, base._advance, base._release)


@pytest.mark.parametrize("first", ["slice_end", "release_flag"])
def test_single_vertex_deadline_is_a_stop_never_carve_exceeded(tmp_path: Path, first: str) -> None:
    """P4, both orderings of the one instant, made deterministic under the manual clock: the walk
    reaches the release point with a non-terminal verdict and either sees its slice end first (no
    flag is up: the carve-delay ordering that used to record `StepEntry(FAILED, CARVE_EXCEEDED)`)
    or sees the release-point flag first. Either way the record is the same: exactly one `end`,
    `cut=stopped`, the last verdict's condition, and no `CARVE_EXCEEDED` step or code."""
    clocks: list[kit.ManualClock] = []
    rig = tk.tree_rig(tmp_path, _solo_never_ready(clocks), {}, deadline_s=SOLO_DEADLINE_S)
    clocks.append(rig.rig.clock)
    release_point = kit.NOW + timedelta(seconds=SOLO_DEADLINE_S - rig.plan.release_slice)
    assert rig.rig.services.slice_end(NodePath(())) == release_point  # the root has no carve
    cancel = rig.rig.cancel
    if first == "release_flag":
        plain = cancel.wait

        def wait(timeout: timedelta) -> bool:
            stopped = plain(timeout)
            if not stopped and rig.rig.clock.now >= release_point:
                cancel.stop = StopCause.RELEASE_POINT  # the flag lands at the same instant
            return stopped

        cancel.wait = wait  # type: ignore[method-assign]
    walked = rig.rig.loop()
    walked.run()
    assert rig.rig.clock.now >= release_point, "the wait ended before the release point"
    rows = rig.rows()
    (end,) = [row for row in rows if row["class"] == "end"]
    assert (end["path"], end["cut"], end["condition"]) == ("", "stopped", "converging"), end
    assert end["code"] != codes.CARVE_EXCEEDED
    assert not [r for r in rows if r["class"] == "step" and r.get("code") == codes.CARVE_EXCEEDED]
    assert walked.goal is Goal.RELEASE
    assert cancel.stop is StopCause.RELEASE_POINT, "the release-point flag is up for the host"
    assert rig.marker.paths("stop") == [""], "the release walk gave back what the root made"
