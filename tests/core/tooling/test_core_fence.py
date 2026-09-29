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

    # every core lane's prefix belongs to [phases].core; every gate sits under a core lane
    prefixes = cfg.phases["core"]
    lane_prefixes = {lane.branch_prefix for lane in lanes}
    assert lane_prefixes <= set(prefixes)
    # a bundle prefix (`wr/core-bundle/`) may be listed in [phases] before or without its lane
    assert set(prefixes) - lane_prefixes <= {p for p in prefixes if p.endswith("-bundle/")}
    for gate in gates:
        assert any(gate.branch.startswith(p) for p in prefixes), gate.branch

    # every core gate branch is a full branch name matching exactly one gate (CM-2)
    for gate in gates:
        assert not gate.branch.endswith("/"), gate.branch
        assert fence_mod.match_gate(gate.branch, cfg.gates) is gate
    assert len({g.merge for g in gates}) == len(gates)

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
