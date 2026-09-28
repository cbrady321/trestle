"""SA-11 drift proof: the fence's real config never lets a lane touch a
Leave target, and gate order matches phase merge order (L.P0-0d.2)."""

from __future__ import annotations

import pytest

from tests.proof import fence as fence_mod

EXTEND_IN_PLACE_TARGETS = [
    # BFD-28 and BFD-38 are Extend in place (root B.1), so no `leave` glob
    # may cover their targets; symbol-level Leaves are covered by d1
    # facets (tools_list, query_row_shapes, spec_keys), not a file glob.
    "trestle/server/main.py",
    "trestle/query/views.py",
    "trestle/query/fs.py",
]


@pytest.mark.parametrize("sa", ["SA-11"])
def test_lane_globs_avoid_leave(sa: str) -> None:
    cfg = fence_mod.load_fence()
    for lane in cfg.lanes:
        for leave_glob in cfg.leave:
            # every lane glob must not be swallowed by a leave glob:
            # a lane's globs are files the lane may edit, so none of them
            # should resolve into a `leave` path.
            for g in lane.globs:
                if g.startswith("!"):
                    continue
                assert not fence_mod.glob_match(g.rstrip("*").rstrip("/"), [leave_glob]), (
                    f"lane {lane.name}'s glob {g!r} collides with leave glob {leave_glob!r}"
                )


@pytest.mark.parametrize("sa", ["SA-11"])
def test_gate_order_matches_phase_order(sa: str) -> None:
    cfg = fence_mod.load_fence()
    p0_gates = [g for g in cfg.gates if g.phase == "p0"]
    order = [g.merge for g in p0_gates]
    spine_order = [m for m in order if m in ("P0-0a", "P0-0b", "P0-0c", "P0-0d")]
    assert spine_order == ["P0-0a", "P0-0b", "P0-0c", "P0-0d"]
    by_merge = {g.merge: g for g in p0_gates}
    assert by_merge["P0-0b"].requires_merge == ["P0-0a"]
    assert by_merge["P0-0c"].requires_merge == ["P0-0b"]
    assert by_merge["P0-0d"].requires_merge == ["P0-0c"]
    for lane_merge in ("P0-1A", "P0-1B", "P0-1C", "P0-1D", "P0-1E"):
        assert by_merge[lane_merge].requires_merge == ["P0-0d"]
    assert set(by_merge["J0"].requires_merge) == {
        "P0-0d",
        "P0-1A",
        "P0-1B",
        "P0-1C",
        "P0-1D",
        "P0-1E",
    }


@pytest.mark.parametrize("sa", ["SA-11"])
def test_leave_globs_avoid_extend_in_place_targets(sa: str) -> None:
    cfg = fence_mod.load_fence()
    for target in EXTEND_IN_PLACE_TARGETS:
        assert not fence_mod.glob_match(target, cfg.leave), (
            f"leave globs must not cover Extend-in-place target {target!r}"
        )
