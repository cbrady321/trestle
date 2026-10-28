"""SA-11 drift proof for Slice B's fence fragment (L.NW-1.1): no B lane glob resolves into a
`leave` glob, and B's gate order encodes the merge order (NW-1 after J-CORE and CK-3/4, NW-2 after
SV-5 and SL-3, RB-0 after J-SLICE-A and NW-2, J-SLICE-B after RB-12). Permanent, like the fence it
reads (DM-77).

Bundle form: the 16 product gates share the branch `wr/slice-b-bundle/slice-b` (`match_gates` lands
them in requires_merge order), so this file reads `cfg.gates` directly and never `match_gate` on the
bundle branch (AMB-14; core's `tests/proof/drift/test_sa11_fence.py` does the same)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tests.proof import fence as fence_mod

FENCE_D = Path(__file__).resolve().parents[2] / "fence.d"

# the plan's merge order for the 16 product gates (b-slice-b.md § Merge order and gates)
PRODUCT_ORDER = [
    "NW-1", "NW-2", "RB-0", "NW-3", "RB-1", "RB-2", "RB-3", "RB-4", "RB-5", "RB-6",
    "RB-7", "RB-8", "RB-9", "RB-10", "RB-11", "RB-12",
]  # fmt: skip

# merges other phases land before B (they are on master, not gates of this fragment)
EXTERNAL = {"J-CORE", "CK-3/4", "SV-5", "SL-3", "J-SLICE-A"}


def _slice_b(cfg: fence_mod.FenceConfig):
    lanes = [lane for lane in cfg.lanes if lane.phase == "slice-b"]
    gates = [gate for gate in cfg.gates if gate.phase == "slice-b"]
    return lanes, gates


def _leave_hits(lanes: list[fence_mod.Lane], leave: list[str]) -> list[tuple[str, str]]:
    hits = []
    for lane in lanes:
        for g in lane.globs:
            if g.startswith("!"):
                continue
            if fence_mod.glob_match(g.rstrip("*").rstrip("/"), leave):
                hits.append((lane.name, g))
    return hits


@pytest.mark.parametrize("sa", ["SA-11"])
def test_b_lane_globs_avoid_leave(sa: str) -> None:
    cfg = fence_mod.load_fence()
    lanes, _ = _slice_b(cfg)
    assert {lane.name for lane in lanes} == {
        "b-legacy", "b-adapters", "b-env", "b-ckpt", "slice-b-bundle",
    }  # fmt: skip
    assert _leave_hits(lanes, cfg.leave) == []


@pytest.mark.parametrize("sa", ["SA-11"])
def test_b_gate_order_encodes_merge_order(sa: str) -> None:
    cfg = fence_mod.load_fence()
    _, gates = _slice_b(cfg)
    by_merge = {g.merge: g for g in gates}
    assert len(by_merge) == len(gates)

    # the fragment's table order: the 16 product gates in merge order, then RB-13, then J-SLICE-B
    assert [g.merge for g in gates] == [*PRODUCT_ORDER, "RB-13", "J-SLICE-B"]
    # a predecessor is an external merge or an EARLIER gate of the fragment
    seen: set[str] = set()
    for gate in gates:
        assert gate.requires_merge, gate.merge
        assert set(gate.requires_merge) <= (EXTERNAL | seen), (gate.merge, gate.requires_merge)
        seen.add(gate.merge)

    assert by_merge["NW-1"].requires_merge == ["J-CORE", "CK-3/4"]
    assert by_merge["NW-2"].requires_merge == ["SV-5", "SL-3"]
    assert by_merge["RB-0"].requires_merge == ["J-SLICE-A", "NW-2"]
    assert by_merge["J-SLICE-B"].requires_merge == ["RB-12"]
    assert by_merge["RB-13"].requires_merge == ["RB-12"]

    # the bundle branch lands the product gates in exactly that order
    matched = fence_mod.match_gates("wr/slice-b-bundle/slice-b", cfg.gates)
    assert [g.merge for g in matched] == PRODUCT_ORDER


def _planted(tmp_path: Path, mutate) -> fence_mod.FenceConfig:
    frag = tmp_path / "fence.d"
    shutil.copytree(FENCE_D, frag)
    target = frag / "slice-b.toml"
    target.write_text(mutate(target.read_text()))
    return fence_mod.load_fence(d_dir=frag)


@pytest.mark.parametrize("sa", ["SA-11"])
def test_planted_glob_inside_leave_is_detected(sa: str, tmp_path: Path) -> None:
    cfg = fence_mod.load_fence()
    assert cfg.leave, "fence.toml carries leave globs"
    planted_glob = cfg.leave[0].replace("**", "x")
    cfg = _planted(
        tmp_path,
        lambda text: text.replace(
            'name          = "b-ckpt"',
            'name          = "b-ckpt"\n# planted',
            1,
        ).replace(
            '"tests/proof/reviews/*-slice-b.toml"]',
            f'"tests/proof/reviews/*-slice-b.toml", "{planted_glob}"]',
            1,
        ),
    )
    lanes, _ = _slice_b(cfg)
    assert ("b-ckpt", planted_glob) in _leave_hits(lanes, cfg.leave)


@pytest.mark.parametrize("sa", ["SA-11"])
def test_planted_reversed_gate_edge_is_detected(sa: str, tmp_path: Path) -> None:
    # NW-3 requiring RB-1 (a LATER gate) reverses the merge order
    cfg = _planted(
        tmp_path,
        lambda text: text.replace(
            'merge          = "NW-3"\nrequires_merge = ["RB-0"]',
            'merge          = "NW-3"\nrequires_merge = ["RB-1"]',
            1,
        ),
    )
    _, gates = _slice_b(cfg)
    seen: set[str] = set()
    violations = []
    for gate in gates:
        if not set(gate.requires_merge) <= (EXTERNAL | seen):
            violations.append(gate.merge)
        seen.add(gate.merge)
    assert violations == ["NW-3"]
