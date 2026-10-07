"""L.CS-1.3: the core fence fragment under P0's closed CM-2 loader. The only
core test that reads `tests/proof/fence.py` or `fence.d/`; the fence is
permanent (CM-11), so this file is never deleted."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.proof import fence as fence_mod


def _core(cfg: fence_mod.FenceConfig):
    lanes = [lane for lane in cfg.lanes if lane.phase == "core"]
    gates = [gate for gate in cfg.gates if gate.phase == "core"]
    return lanes, gates


def test_core_fence_fragment_lanes_and_gates(tmp_path: Path) -> None:
    cfg = fence_mod.load_fence()
    lanes, gates = _core(cfg)
    assert lanes and gates

    # [phases].core lists exactly the core lanes' prefixes (the bundle prefix `wr/core-bundle/`
    # has its lane); every gate sits under exactly one core lane (R4 and the CK drill read it)
    prefixes = cfg.phases["core"]
    lane_prefixes = {lane.branch_prefix for lane in lanes}
    assert lane_prefixes == set(prefixes)
    for gate in gates:
        owners = [lane.name for lane in lanes if gate.branch.startswith(lane.branch_prefix)]
        assert len(owners) == 1, (gate.branch, owners)

    # every core gate branch is a full branch name (CM-2). A branch carrying one gate matches
    # exactly that gate; the bundle branch carries the 16 product gates, and `match_gates`
    # lands them in the table's order (the merge order checked below)
    by_branch: dict[str, list[fence_mod.Gate]] = {}
    for gate in gates:
        assert not gate.branch.endswith("/"), gate.branch
        by_branch.setdefault(gate.branch, []).append(gate)
    for branch, members in by_branch.items():
        matched = fence_mod.match_gates(branch, cfg.gates)
        assert [id(g) for g in matched] == [id(g) for g in members], branch
        if len(members) == 1:
            assert fence_mod.match_gate(branch, cfg.gates) is members[0]
    assert len({g.merge for g in gates}) == len(gates)
    assert [g.merge for g in by_branch["wr/core-bundle/core"]] == [
        "CS-2", "CK-8", "CS-3", "CS-4", "CK-14", "CL-D1", "CL-A1", "CL-C1",
        "CK-1", "CL-C2", "CK-3/4", "CL-B2", "CL-B1", "CL-B3", "CL-A2", "CL-PX2",
    ]  # fmt: skip
    with pytest.raises(fence_mod.GateMatchError, match="use match_gates"):
        fence_mod.match_gate("wr/core-bundle/core", cfg.gates)

    # R4 judges every bundle chunk against the bundle lane: its globs hold every product lane's
    (bundle_lane,) = [lane for lane in lanes if lane.name == "core-bundle"]
    assert bundle_lane.branch_prefix == "wr/core-bundle/"
    for lane in lanes:
        if lane.name not in ("core-bundle", "core-ckpt"):
            missing = [g for g in lane.globs if g not in bundle_lane.globs]
            assert not missing, (lane.name, missing)

    # gate order encodes merge order: a gate's predecessors are J0 or earlier gates
    seen: set[str] = {"J0"}
    for gate in gates:
        assert gate.requires_merge, gate.branch
        assert set(gate.requires_merge) <= seen, (gate.merge, gate.requires_merge)
        seen.add(gate.merge)
    assert gates[0].merge == "CS-1" and gates[0].requires_merge == ["J0"]

    # no core lane's glob resolves into a B.1 Leave glob (SA-11)
    for lane in lanes:
        for g in lane.globs:
            if g.startswith("!"):
                continue
            assert not fence_mod.glob_match(g.rstrip("*").rstrip("/"), cfg.leave), (lane.name, g)

    # the checkpoint lane holds only the J-CORE gate, its globs exactly CM-5's
    (ckpt,) = [lane for lane in lanes if lane.name == "core-ckpt"]
    assert sorted(ckpt.globs) == [
        "tests/fixtures/fossils/core/**",
        "tests/proof/reviews/*-core.toml",
    ]
    ckpt_gates = [g for g in gates if g.branch.startswith(ckpt.branch_prefix)]
    assert [g.merge for g in ckpt_gates] == ["J-CORE"]
    assert ckpt_gates[0].branch == "wr/core-ckpt/j-core"

    # the schema is closed: a planted withdrawn key fails the load
    frag = tmp_path / "fence.d"
    frag.mkdir()
    (frag / "core.toml").write_text(
        'phase = "core"\n[[lane]]\nname = "x"\nbranch_prefix = "wr/x/"\n'
        'globs = ["a"]\nhot = ["a"]\n'
    )
    with pytest.raises(fence_mod.FenceLoadError, match="withdrawn"):
        fence_mod.load_fence(d_dir=frag)
