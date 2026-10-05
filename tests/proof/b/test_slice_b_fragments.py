"""L.NW-1.1: the Slice B phase fragments under P0's exact loaders (CM-2 fence, CSC-1 labels, CM-7
register). The B counterpart of `tests/core/tooling/test_core_fence.py` and A-1's
`tests/single/docs/test_single_fragments.py`; the fence is permanent (CM-2), so this file is never
deleted. SA-11's glob and merge-order checks live in `tests/proof/drift/b/test_sa11_b_fence.py`.

Bundle form: the 16 product gates share one branch, `wr/slice-b-bundle/slice-b`; RB-13 (conditional)
and J-SLICE-B each keep their own branch."""

from __future__ import annotations

import re
import tomllib
from argparse import Namespace
from pathlib import Path

import pytest

from tests.proof import fence as fence_mod
from tests.proof import meta, register

ROOT = Path(__file__).resolve().parents[3]
PROOF = ROOT / "tests" / "proof"
BUNDLE_BRANCH = "wr/slice-b-bundle/slice-b"
LEAF_ID = re.compile(r"^L\.[A-Za-z0-9/-]+\.\d+$")
TWIN = "@stub-twin"

# Deferred core labels B declares as the closing phase (CM-8, DM-63, DM-19) -> the closing leaf.
DEFERRED_CORE = {
    "WR-CANCEL-5:adapter-contract-suite": "L.NW-2.9",
    "WR-OWN-1:created-container-released": "L.RB-12.3",
    "WR-OWN-3:created-container-stopped-on-success": "L.RB-12.6",
    "WR-OWN-6:docker-inventory": "L.RB-12.5",
}


def _slice_b(cfg: fence_mod.FenceConfig):
    lanes = [lane for lane in cfg.lanes if lane.phase == "slice-b"]
    gates = [gate for gate in cfg.gates if gate.phase == "slice-b"]
    return lanes, gates


def test_fence_fragment_lanes_and_gates(tmp_path: Path) -> None:
    cfg = fence_mod.load_fence()
    lanes, gates = _slice_b(cfg)
    assert lanes and gates

    # [phases].slice-b lists exactly the B lanes' prefixes (the bundle prefix has its lane); every
    # gate sits under exactly one B lane (R4 reads it)
    lane_prefixes = {lane.branch_prefix for lane in lanes}
    assert lane_prefixes == set(cfg.phases["slice-b"])
    for gate in gates:
        owners = [lane.name for lane in lanes if gate.branch.startswith(lane.branch_prefix)]
        assert len(owners) == 1, (gate.branch, owners)

    # every B gate branch is a full branch name (CM-2). The bundle branch carries the 16 product
    # gates, which `match_gates` lands in requires_merge order; RB-13 and J-SLICE-B are alone
    by_branch: dict[str, list[fence_mod.Gate]] = {}
    for gate in gates:
        assert not gate.branch.endswith("/"), gate.branch
        by_branch.setdefault(gate.branch, []).append(gate)
    assert set(by_branch) == {BUNDLE_BRANCH, "wr/b-env/rb-13", "wr/b-ckpt/j-slice-b"}
    assert len(by_branch[BUNDLE_BRANCH]) == 16
    for branch, members in by_branch.items():
        matched = fence_mod.match_gates(branch, cfg.gates)
        assert [id(g) for g in matched] == [id(g) for g in members], branch
        assert all(g.phase == "slice-b" for g in matched), branch
        if len(members) == 1:
            assert fence_mod.match_gate(branch, cfg.gates) is members[0]
    assert len({g.merge for g in gates}) == len(gates) == 18
    with pytest.raises(fence_mod.GateMatchError, match="use match_gates"):
        fence_mod.match_gate(BUNDLE_BRANCH, cfg.gates)
    assert [g.merge for g in by_branch["wr/b-env/rb-13"]] == ["RB-13"]
    assert [g.merge for g in by_branch["wr/b-ckpt/j-slice-b"]] == ["J-SLICE-B"]

    # R4 judges every bundle chunk against the bundle lane: its globs hold every product lane's
    (bundle_lane,) = [lane for lane in lanes if lane.name == "slice-b-bundle"]
    assert bundle_lane.branch_prefix == "wr/slice-b-bundle/"
    product = [lane for lane in lanes if lane.name not in ("slice-b-bundle", "b-ckpt")]
    assert {lane.name for lane in product} == {"b-legacy", "b-adapters", "b-env"}
    for lane in product:
        missing = [g for g in lane.globs if g not in bundle_lane.globs]
        assert not missing, (lane.name, missing)
    # FF-2: paths the leaves need that no plan lane owns
    for extra in (
        "tests/proof/temporary.d/p0.toml",
        "tests/proof/divergence.toml",
        "tests/proof/ci_shards.py",
        "tests/proof/selftest/test_ci_checkout.py",
        "tests/proof/drift/b/__init__.py",
        "tests/proof/b/__init__.py",
        "tests/pins/e_packs/driver_g_e3.py",
        "tests/pins/e_packs/fake_backend.py",
        "tests/proof/host/docker_gate/__init__.py",
        "tests/core/docs/test_cl_d1_deferrals.py",
    ):
        assert extra in bundle_lane.globs, extra
    # RB-13.3 removes TM-P0-2:G-E2 on its own branch (lane b-env) when NW-2.8 could not (AMB-12)
    (b_env,) = [lane for lane in lanes if lane.name == "b-env"]
    assert "tests/proof/temporary.d/p0.toml" in b_env.globs

    # a B lane globs only the B fence fragment
    for lane in lanes:
        for g in lane.globs:
            if g.startswith("tests/proof/fence.d/"):
                assert g == "tests/proof/fence.d/slice-b.toml", (lane.name, g)

    # the checkpoint lane holds only the J-SLICE-B gate, its globs exactly CM-5's
    (ckpt,) = [lane for lane in lanes if lane.name == "b-ckpt"]
    assert sorted(ckpt.globs) == [
        "tests/fixtures/fossils/slice-b/**",
        "tests/proof/reviews/*-slice-b.toml",
    ]

    # the schema is closed: a planted withdrawn key fails the load
    frag = tmp_path / "fence.d"
    frag.mkdir()
    (frag / "slice-b.toml").write_text(
        'phase = "slice-b"\n[[lane]]\nname = "x"\nbranch_prefix = "wr/x/"\n'
        'globs = ["a"]\nhot = ["a"]\n'
    )
    with pytest.raises(fence_mod.FenceLoadError, match="withdrawn"):
        fence_mod.load_fence(d_dir=frag)


def _labels() -> list[dict]:
    return list(tomllib.loads((PROOF / "labels.d" / "slice-b.toml").read_text())["label"])


def test_labels_rows_exist_and_postures_valid() -> None:
    labels = _labels()
    assert len(labels) == 186  # 136 labels + 50 twins (plan § Coverage (a))
    by_id = {str(label["id"]): label for label in labels}
    assert len(by_id) == len(labels)
    rows = {r["id"] for r in tomllib.loads(meta.ROW_OWNERS_PATH.read_text())["row"]}
    oqs = {o["id"] for o in meta.load_open_questions()}
    for label in labels:
        lid = str(label["id"])
        assert set(label) <= meta.CSC1_ALL_KEYS, lid
        assert meta.CSC1_REQUIRED_KEYS <= set(label), lid
        assert label["posture"] in meta.CSC1_POSTURES, lid
        assert lid.split(":", 1)[0] == label["row"], lid
        assert label["row"] in rows, lid
        assert label["step"] == "B", lid
        assert label["slice"] == ("core" if lid in DEFERRED_CORE else "B"), lid
        assert label["venue"] in ("CI", "HOST", "BOTH"), lid
        assert LEAF_ID.match(str(label["declared_by"])), lid
        # a pure DOCKER-tier label is venue HOST, and a HOST label has a DOCKER tier (CSC-9)
        assert label["tier"] != "DOCKER" or label["venue"] == "HOST", lid
        assert label["venue"] != "HOST" or "DOCKER" in str(label["tier"]).split("+"), lid
        if label["posture"] in ("gated_on", "both_variant"):
            assert label["oq"] in oqs, lid
        else:
            assert "oq" not in label, lid
        assert ("reason" in label) == (label["posture"] == "na"), lid
        # a twin is STUB . CI . stub_proven with a base label declared by the same leaf (CSC-8)
        if lid.endswith(TWIN):
            base = by_id.get(lid[: -len(TWIN)])
            assert base is not None, lid
            assert (label["tier"], label["venue"], label["posture"]) == (
                "STUB",
                "CI",
                "stub_proven",
            )
            assert base["declared_by"] == label["declared_by"], lid
            assert base["venue"] == "HOST", lid  # only HOST nodes have CI twins here

    # the four deferred core labels are declared here, by their closing leaves, as claims (CM-8)
    for lid, leaf in DEFERRED_CORE.items():
        assert by_id[lid]["declared_by"] == leaf, lid
        assert by_id[lid]["posture"] == "claim", lid

    # every label id is unique across all labels.d fragments, and the real registry loads
    ids = [str(label["id"]) for label in meta._load_all_labels()]
    assert len(ids) == len(set(ids)), sorted({i for i in ids if ids.count(i) > 1})
    assert set(by_id) <= set(ids)
    assert meta.cmd_audit_rows(Namespace()) == 0


def test_temporary_fragment_loads_and_names_its_introducers() -> None:
    path = PROOF / "temporary.d" / "slice-b.toml"
    text = path.read_text()
    header = text.split("[[entry]]", 1)[0]
    assert header.lstrip().startswith("# tests/proof/temporary.d/slice-b.toml")
    assert "CM-7" in header
    entries = tomllib.loads(text)["entry"]
    assert [e["id"] for e in entries] == [
        "TM-B4-1.1", "TM-B4-1.2", "TM-B4-1.3", "TM-B4-1.4", "TM-B4-3",
    ]  # fmt: skip
    assert {e["introduced_by"] for e in entries} == {
        "L.RB-4.1", "L.RB-7.2", "L.RB-9.1", "L.RB-10.2", "L.RB-0.2",
    }  # fmt: skip
    for entry in entries:
        assert entry["permanent"] is False, entry["id"]
    by_id = {e["id"]: e for e in entries}
    assert by_id["TM-B4-3"]["removed_by"] == "L.RB-1.1"
    for tid in ("TM-B4-1.1", "TM-B4-1.2", "TM-B4-1.3", "TM-B4-1.4"):
        assert by_id[tid]["removed_by"] == "named-not-removed" and by_id[tid]["serves"] == []
    # the fragment loads under the register's loader alongside every other fragment, and an
    # entry whose introducing leaf has not landed reads as absent (its probe is false)
    loaded = {e["id"]: e for e in register.load_entries()}
    assert set(by_id) <= set(loaded)
