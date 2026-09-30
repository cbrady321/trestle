"""L.TR-4.2: release across levels in plan rank, by the loop's release walk and by the host sweep.

A root releases what every node under it created, in one order: descending release rank of the
creating node (a node's rank is one above the highest rank it `needs`), parallel only within a
rank, and never before the root's release phase (V-4.4, WR-UNIT-5). A mid-run recreate is a
repair of a resource the run created, never a release (WR-OWN-9, WR-REMEDY-4).

Two releasers, one order. The loop's walk is read from a tree run in-library (MC-26's rig: the real
lane under a manual clock). The host sweep is read from the same run's record with the plugin dead
(its `release` gives nothing back, so the record holds only claims), swept from the record alone
against the sweep stub's engine (`tests/single/control/sweep_stub.py`): no plugin code, so the
plugin cannot be what orders it. `test_depth1_sweep_order_golden` is the leaf's own pin: the
one-rank order (reverse issue order) that a plan of one vertex has always had must not move."""

from __future__ import annotations

import threading
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from tests.proof import tolerances
from tests.single.control import sweep_stub as sw
from tests.tree import treekit as tk
from tests.tree.sharedkit import SharedMarker, observing_unit
from trestle.common import lane_format as lf
from trestle.common.plan.compiler import implicit_depth1_plan
from trestle.server import fold, sweep

proves_order = pytest.mark.proves(
    "WR-UNIT-5", "WR-UNIT-5:release-reverse-dependency-order", "A", "tree", "PROC", "CI"
)
proves_phase = pytest.mark.proves(
    "WR-UNIT-5", "WR-UNIT-5:no-release-before-root-phase", "A", "tree", "PROC", "CI"
)
proves_recreate = pytest.mark.proves(
    "WR-UNIT-5", "WR-UNIT-5:recreate-not-release", "A", "tree", "PROC", "CI"
)

UP = "up"
K_A, K_B, K_C, K_D = (f"sel-{p}/{UP}" for p in ("mid/a", "mid/b", "c", "d"))


def _three_levels(tmp_path: Path, *, dead: bool = False) -> tk.TreeRig:
    """`app{mid{a, b needs a}, c needs mid, d}` run one leaf at a time, so the nodes create in the
    order a, b, c, d: `a` has rank 0, `b` rank 1, `c` rank 2 and `d` rank 0, at depths 3, 3, 2 and
    2. Rank order (c, b, then d, a) is neither reverse issue order (d, c, b, a) nor reverse plan
    order, so a releaser that ignored rank could not pass. `dead`: no node gives anything back
    itself."""
    mid = tk.group("mid", (tk.bind("a"), tk.bind("b", "a")), concurrency=2, budget_s=100)
    app = tk.group(
        "app",
        (tk.bind("mid"), tk.bind("c", "mid"), tk.bind("d")),
        concurrency=1,
        budget_s=400,
    )
    units: dict[str, object] = {
        "mid": mid,
        **{n: observing_unit(n, releases=not dead) for n in ("a", "b", "c", "d")},
    }
    return tk.tree_rig(tmp_path, app, units, SharedMarker())


def _swept(rig: tk.TreeRig, engine: sw.Engine, **overrides: Any) -> sweep.CleanupDisposition:
    """The host's sweep over the run's folded lane and admitted plan, plugin code never involved."""
    record = fold.fold_lane(rig.run_dir, rig.plan)
    return sw.run_sweep(record, engine, plan=rig.plan, **overrides)


def _first_looks(engine: sw.Engine) -> list[str]:
    """The targets in the order the sweep took them: each target's commands open with an observe."""
    seen: list[str] = []
    for role, name in engine.calls:
        if role == "obs" and name not in seen:
            seen.append(name)
    return seen


def test_depth1_sweep_order_golden() -> None:
    """The own pin. A plan of one vertex has one rank: its targets go in reverse issue order, one
    target's commands together, and the process-group target closes the list. The same three
    orders result with no plan, the implicit depth-1 plan and a recorded one-vertex plan."""
    expected: list[tuple[str, str]] = []  # four commands per target
    for name in ("c", "b", "a"):
        expected += [("obs", name), ("stop", name), ("rm", name), ("obs", name)]
    for plan in (None, implicit_depth1_plan("root")):
        engine = sw.Engine(present={"a", "b", "c"})
        record = sw.folded(sw.ticket("a"), sw.ticket("b"), sw.ticket("c"))
        disposition = sw.run_sweep(record, engine, plan=plan)
        assert engine.calls == expected
        assert sw.names(disposition.released) == ["c", "b", "a", None]
    # and the rows the ledger keeps are in that order, none for the group target
    result = sweep.sweep_detailed(
        sw.folded(sw.ticket("a"), sw.ticket("b"), sw.ticket("c")),
        sw.Group(),
        sw.BUDGET,
        sw.LIMITS,
        implicit_depth1_plan("root"),
        io=sw.Engine(present={"a", "b", "c"}).io(),
    )
    assert [(r.target.effect, r.form, r.disposition) for r in result.rows] == [
        ("c", sweep.FORM_ARGV, sweep.RELEASED),
        ("b", sweep.FORM_ARGV, sweep.RELEASED),
        ("a", sweep.FORM_ARGV, sweep.RELEASED),
    ]


@proves_order
def test_release_order_equals_plan_rank_multi_level(tmp_path: Path) -> None:
    """The loop's walk gives back `c`'s resource (rank 2), then `b`'s (rank 1), then the two rank-0
    resources (`d`, the later, first), across three levels, each after every vertex has its end;
    the host sweep of a record left by a dead plugin takes the same targets in the same order and
    releases every one."""
    rig = _three_levels(tmp_path / "loop")
    rig.run()
    rank = rig.plan.release_rank
    assert (rank["c"], rank["mid/b"], rank["mid/a"], rank["d"]) == (2, 1, 0, 0)
    assert [r["path"] for r in rig.rows() if r["class"] == "confirmation"][:4] == [
        "mid/a",
        "mid/b",
        "c",
        "d",
    ]  # the issue order, which is not the release order
    released = [row["path"] for row in rig.rows() if row["class"] == "released"]
    assert released == ["c", "mid/b", "d", "mid/a"]
    assert [rank[p] for p in released] == sorted((rank[p] for p in released), reverse=True)

    dead = _three_levels(tmp_path / "dead", dead=True)
    dead.run()
    assert not [row for row in dead.rows() if row["class"] == "released"]  # nothing gave back
    engine = sw.Engine(present={K_A, K_B, K_C, K_D})
    disposition = _swept(dead, engine)
    assert _first_looks(engine) == [K_C, K_B, K_D, K_A]  # descending rank, every level
    assert sw.names(disposition.released) == [UP, UP, UP, UP, None]
    assert not disposition.unknown and not engine.present  # gone: observed absent after the stop


@proves_order
def test_sweep_parallel_only_within_a_rank(tmp_path: Path) -> None:
    """`x` and `y` (rank 0) and `z` (rank 1, needs both): with two workers the two rank-0 targets
    are released together (each waits inside its stop for the other, so a sweep that ran them one
    at a time could not pass), and `z`, the higher rank, is done before either of them starts."""
    together = threading.Barrier(2, timeout=tolerances.JOIN_WAIT_S)
    lock = threading.Lock()
    root = tk.group("app", (tk.bind("x"), tk.bind("y"), tk.bind("z", "x", "y")), concurrency=2)
    units = {n: observing_unit(n, releases=False) for n in ("x", "y", "z")}
    rig = tk.tree_rig(tmp_path, root, units, SharedMarker())
    rig.run()
    assert (rig.plan.release_rank["x"], rig.plan.release_rank["z"]) == (0, 1)

    engine = sw.Engine(present={f"sel-{n}/{UP}" for n in ("x", "y", "z")})
    plain = engine.run
    log: list[tuple[str, str]] = []

    def run(release: lf.ArgvRelease, argv: Any, timeout_s: float) -> sweep.CommandResult:
        with lock:
            log.append((argv[0], argv[1]))
        if argv[0] == "stop" and argv[1] in (f"sel-x/{UP}", f"sel-y/{UP}"):
            together.wait()
        return plain(release, argv, timeout_s)

    limits = replace(sw.LIMITS, sweep_parallelism=2)
    disposition = _swept(rig, engine, limits=limits, io=sweep.SweepIO(run=run))
    assert len(disposition.released) == 4 and not disposition.unknown  # x, y, z and the group
    last_z = max(n for n, (_, name) in enumerate(log) if name == f"sel-z/{UP}")
    first_low = min(n for n, (_, name) in enumerate(log) if name != f"sel-z/{UP}")
    assert last_z < first_low  # a rank is done before the next one starts


@proves_phase
def test_no_release_before_root_release_phase(tmp_path: Path) -> None:
    """`a` and `f` end (`f` fails) well before `c` starts, and what they made is still there when
    `c` creates its own: nothing is given back until the root's release phase, because a later node
    may depend on it. In the record the first release is after every vertex's end, the root's
    last of all."""
    before_c: dict[str, Any] = {}
    marker = SharedMarker()

    def on_create(path: str, effect: str) -> None:
        if path == "c":
            before_c["live"] = marker.live()
            before_c["rows"] = rig.rows()

    marker.on_create_effect = on_create
    root = tk.group(
        "app", (tk.bind("a"), tk.bind("f"), tk.bind("c", "a")), concurrency=3, budget_s=100
    )
    units = {
        "a": observing_unit("a"),
        "f": observing_unit("f", fails_after_create=True),
        "c": observing_unit("c"),
    }
    rig = tk.tree_rig(tmp_path, root, units, marker)
    rig.run()

    assert {f"sel-a/{UP}", f"sel-f/{UP}"} <= before_c["live"]  # still there, `f` failed long ago
    assert not [r for r in before_c["rows"] if r["class"] in ("released",)]
    assert not [r for r in before_c["rows"] if r["class"] == "issue" and r["effect"] == "stop"]
    rows = rig.rows()
    ends = [r["seq"] for r in rows if r["class"] == "end"]
    released = [r["seq"] for r in rows if r["class"] == "released"]
    stops = [r["seq"] for r in rows if r["class"] == "issue" and r["effect"] == "stop"]
    assert released and stops
    assert min(released) > max(ends) and min(stops) > max(ends)
    assert max(ends) == next(r["seq"] for r in rows if r["class"] == "end" and r["path"] == "")
    assert marker.live() == frozenset()  # and all of it went at the release phase


@proves_recreate
def test_recreate_is_not_release(tmp_path: Path) -> None:
    """`a`'s resource turns unhealthy and the declared remedy recreates it mid-run. That is a repair
    of a resource the run created: no release entry and no stop ticket before the release phase, the
    handle still in the root's release set, the recreate ticket carrying the claim's own descriptor
    (so the host sees one target, never two), and one release of it at the end."""
    marker = SharedMarker()
    at_recreate: dict[str, Any] = {}

    def on_create(path: str, effect: str) -> None:
        marker.hot.add(f"sel-{path}/{effect}")  # unhealthy until recreated

    def during_recreate() -> None:
        at_recreate["rows"] = rig.rows()

    marker.on_create_effect = on_create
    marker.hook["recreate"] = during_recreate
    root = tk.group("app", (tk.bind("a"), tk.bind("b", "a")), concurrency=2)
    units = {
        "a": observing_unit("a", remedy=True),
        "b": observing_unit("b", remedy=True),
    }
    rig = tk.tree_rig(tmp_path, root, units, marker)
    rig.run()

    assert marker.recreated == [f"sel-a/{UP}", f"sel-b/{UP}"]  # each repaired once, in place
    rows = rig.rows()
    claim = next(
        r for r in rows if r["class"] == "issue" and r["path"] == "a" and r["effect"] == UP
    )
    repair = next(
        r for r in rows if r["class"] == "issue" and r["path"] == "a" and r["effect"] == "recreate"
    )
    assert repair["release"] == claim["release"]  # one target for the host, not two
    seen = at_recreate["rows"]
    assert not [r for r in seen if r["class"] == "released"]
    assert not [r for r in seen if r["class"] == "issue" and r["effect"] == "stop"]
    released = [(r["path"], r["effect"]) for r in rows if r["class"] == "released"]
    assert sorted(released) == [("a", UP), ("b", UP)]  # each exactly once, by the release pass
    assert marker.paths("stop") == ["b", "a"]  # descending rank, and the recreate stopped nothing

    dead = tk.tree_rig(
        tmp_path / "dead",
        root,
        {n: observing_unit(n, remedy=True, releases=False) for n in ("a", "b")},
        _hot_marker(),
    )
    dead.run()
    engine = sw.Engine(present={f"sel-a/{UP}", f"sel-b/{UP}"})
    _swept(dead, engine)
    assert _first_looks(engine) == [f"sel-b/{UP}", f"sel-a/{UP}"]
    assert engine.roles(f"sel-a/{UP}") == [
        "obs",
        "stop",
        "rm",
        "obs",
    ]  # the recreate added no target


def _hot_marker() -> SharedMarker:
    marker = SharedMarker()
    marker.on_create_effect = lambda path, effect: marker.hot.add(f"sel-{path}/{effect}")
    return marker
