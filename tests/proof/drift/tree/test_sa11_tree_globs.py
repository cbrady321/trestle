"""L.TR-0.5, SA-11 (tree half): the A-2 (tree) fence fragment under P0's exact CM-2 loader. The
band's only reader of `tests/proof/fence.py` and `fence.d/`; the fence is permanent (CM-2), so
this file stays valid and is never deleted.

Bundle form (recorded in `_tmp/delivery/A2-TR0-RETURN.md`): the eight product gates TR-0..TR-6 share
one branch, `wr/tree-bundle/tree`, exactly as `tests/core/tooling/test_core_fence.py` reads the core
bundle and `tests/single/docs/test_single_fragments.py` the single bundle. J-SLICE-A keeps its own
branch (a checkpoint id is never bundled). The plan's literal one-branch-per-merge form is
superseded; the `match_gates` reader (not `match_gate`, which refuses a bundle) is used
throughout."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.proof import fence as fence_mod

BUNDLE_BRANCH = "wr/tree-bundle/tree"
CKPT_BRANCH = "wr/tree-ckpt/j-slice-a"

# The landing order, equal to the merge order of plans/a2-tree.md (`## Merge order and gates`).
BUNDLE_ORDER = ["TR-0", "TR-1", "TR-2", "TR-3", "TR-4", "TR-L", "TR-5", "TR-6"]


def _tree(cfg: fence_mod.FenceConfig):
    lanes = [lane for lane in cfg.lanes if lane.phase == "tree"]
    gates = [gate for gate in cfg.gates if gate.phase == "tree"]
    return lanes, gates


@pytest.mark.parametrize("sa", ["SA-11"])
def test_tree_globs_exclude_leave_targets(sa: str, tmp_path: Path) -> None:
    cfg = fence_mod.load_fence()
    lanes, gates = _tree(cfg)
    assert {lane.name for lane in lanes} == {"tree", "tree-bundle", "tree-ckpt"}
    assert gates

    # no tree lane glob reaches a `leave` glob, and a lane globs only the tree fence fragment
    for lane in lanes:
        for g in lane.globs:
            if g.startswith("!"):
                continue
            assert not fence_mod.glob_match(g.rstrip("*").rstrip("/"), cfg.leave), (lane.name, g)
            if g.startswith("tests/proof/fence.d/"):
                assert g == "tests/proof/fence.d/tree.toml", (lane.name, g)
    # the Leave globs of base fence.toml stay excluded from every tree lane (a2:L152): a sample path
    # under each Leave glob is not a tree lane path
    assert cfg.leave
    (tree_lane,) = [lane for lane in lanes if lane.name == "tree"]
    for leave_glob in cfg.leave:
        sample = leave_glob.replace("**", "sample.py")
        assert fence_mod.glob_match(sample, cfg.leave), sample
        for lane in lanes:
            assert not fence_mod.glob_match(sample, lane.globs), (lane.name, sample)

    # [phases].tree lists exactly the tree lanes' prefixes, and every tree gate sits under exactly
    # one tree lane (R4 reads it)
    assert {lane.branch_prefix for lane in lanes} == set(cfg.phases["tree"])
    for gate in gates:
        owners = [lane.name for lane in lanes if gate.branch.startswith(lane.branch_prefix)]
        assert len(owners) == 1, (gate.branch, owners)

    # R4 judges every bundle chunk against the bundle lane: its globs hold lane `tree`'s verbatim,
    # plus the paths the plan's lane misses (DAG F-2/AM-15) and core's deferral test
    (bundle_lane,) = [lane for lane in lanes if lane.name == "tree-bundle"]
    assert bundle_lane.branch_prefix == "wr/tree-bundle/"
    missing = [g for g in tree_lane.globs if g not in bundle_lane.globs]
    assert not missing, missing
    for extra in (
        "tests/proof/divergence.toml",
        "tests/proof/differ.py",
        "tests/proof/differ_modes/__init__.py",
        "tests/core/docs/test_cl_d1_deferrals.py",
        "tests/single/contract/test_extract.py",
        "tests/fixtures/workflows/probe_choice_root.py",
    ):
        assert extra in bundle_lane.globs, extra
        assert extra not in tree_lane.globs, extra

    # the checkpoint lane holds only the J-SLICE-A gate, its globs exactly CM-5's slice-a paths
    (ckpt,) = [lane for lane in lanes if lane.name == "tree-ckpt"]
    assert ckpt.branch_prefix == "wr/tree-ckpt/"
    assert sorted(ckpt.globs) == [
        "tests/fixtures/fossils/slice-a/**",
        "tests/proof/reviews/*-slice-a.toml",
    ]
    ckpt_gates = [g for g in gates if g.branch.startswith(ckpt.branch_prefix)]
    assert [g.merge for g in ckpt_gates] == ["J-SLICE-A"]
    assert ckpt_gates[0].branch == CKPT_BRANCH

    # the schema is closed: a planted withdrawn key fails the load
    frag = tmp_path / "fence.d"
    frag.mkdir()
    (frag / "tree.toml").write_text(
        'phase = "tree"\n[[lane]]\nname = "x"\nbranch_prefix = "wr/x/"\n'
        'globs = ["a"]\nhot = ["a"]\n'
    )
    with pytest.raises(fence_mod.FenceLoadError, match="withdrawn"):
        fence_mod.load_fence(d_dir=frag)


@pytest.mark.parametrize("sa", ["SA-11"])
def test_tree_gate_order_encodes_merge_order(sa: str) -> None:
    cfg = fence_mod.load_fence()
    _lanes, gates = _tree(cfg)
    # L.TR-6.fix5: the one prefix gate is the fix route of the landed bundle (its gates resolve only
    # through Bundle-Merge markers): `wr/tree/<fix>` lands TR-6 again, as `WR-Fix: TR-6`
    fix_gates = [g for g in gates if g.branch.endswith("/")]
    assert [(g.branch, g.merge, g.requires_merge) for g in fix_gates] == [
        ("wr/tree/", "TR-6", ["TR-5"])
    ]
    gates = [g for g in gates if g not in fix_gates]
    by_branch: dict[str, list[fence_mod.Gate]] = {}
    for gate in gates:
        by_branch.setdefault(gate.branch, []).append(gate)
    assert set(by_branch) == {BUNDLE_BRANCH, CKPT_BRANCH}

    # `match_gates` (not `match_gate`, which refuses a bundle) returns each branch's own gates
    for branch, members in by_branch.items():
        matched = fence_mod.match_gates(branch, cfg.gates)
        assert [id(g) for g in matched] == [id(g) for g in members], branch
        assert all(g.phase == "tree" for g in matched), branch
    assert [g.merge for g in by_branch[BUNDLE_BRANCH]] == BUNDLE_ORDER
    assert len({g.merge for g in gates}) == len(gates)
    with pytest.raises(fence_mod.GateMatchError, match="use match_gates"):
        fence_mod.match_gate(BUNDLE_BRANCH, cfg.gates)
    assert fence_mod.match_gate(CKPT_BRANCH, cfg.gates) is by_branch[CKPT_BRANCH][0]

    # the gate chain J-SINGLE -> TR-0 -> ... -> TR-6 -> J-SLICE-A equals the merge order: TR-0 needs
    # the previous checkpoint, every other predecessor is the previous merge of the band
    order = by_branch[BUNDLE_BRANCH]
    assert order[0].requires_merge == ["J-SINGLE"]
    previous = "J-SINGLE"
    for gate in order:
        assert gate.requires_merge == [previous], (gate.merge, gate.requires_merge)
        previous = gate.merge
    by_merge = {g.merge: g for g in gates}
    assert by_merge["J-SLICE-A"].requires_merge == ["TR-6"]

    # a checkpoint id is never bundled: J-SLICE-A is on its own lane and branch
    assert all(g.merge != "J-SLICE-A" for g in order)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


@pytest.mark.parametrize("sa", ["SA-11"])
def test_bundle_lane_refuses_planted_out_of_glob_path(sa: str, tmp_path: Path) -> None:
    """R4 (the rule `fence check --pr` applies) over a scratch repo: a path inside the bundle
    lane's globs passes; a planted path outside them and a planted Leave path are refused."""
    cfg = fence_mod.load_fence()
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "master")
    (repo / "README").write_text("base\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    base = _git(repo, "rev-parse", "HEAD")

    def head_with(path: str) -> str:
        _git(repo, "checkout", "-q", "-B", "planted", base)
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("x = 1\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", f"add {path}")
        return _git(repo, "rev-parse", "HEAD")

    warnings: list[str] = []
    inside = head_with("tests/tree/test_planted.py")
    assert fence_mod._tree_rules(cfg, repo, BUNDLE_BRANCH, inside, base, warnings) is None  # noqa: SLF001

    outside = head_with("tests/elsewhere/test_planted.py")
    refused = fence_mod._tree_rules(cfg, repo, BUNDLE_BRANCH, outside, base, warnings)  # noqa: SLF001
    assert refused is not None and refused.rule == "R4"
    assert "outside the lane's globs" in refused.message

    leave = head_with("console/planted.py")
    refused = fence_mod._tree_rules(cfg, repo, BUNDLE_BRANCH, leave, base, warnings)  # noqa: SLF001
    assert refused is not None and refused.rule == "R4"
    assert "is a Leave path" in refused.message
