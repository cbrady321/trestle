"""L.SV-0.2: the A-1 (single) phase fragments under P0's exact loaders (CM-2 fence, CSC-1
labels, CM-7 register). The only A-1 test that reads `tests/proof/fence.py` or `fence.d/`; the
fence is permanent (CM-2), so this file is never deleted.

Bundle form: the 17 product gates share one branch, `wr/single-bundle/single`, exactly as
`tests/core/tooling/test_core_fence.py` reads the core bundle; J-SINGLE keeps its own branch."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

from tests.proof import fence as fence_mod
from tests.proof import meta, register

ROOT = Path(__file__).resolve().parents[3]
PROOF = ROOT / "tests" / "proof"
BUNDLE_BRANCH = "wr/single-bundle/single"
LEAF_ID = re.compile(r"^L\.[A-Za-z0-9/-]+\.\d+$")

# The bundle's landing order (match_gates is greedy over config order with requires_merge; the
# plan's merge-order table L36-55, so SL-11 -- which needs SL-9 -- is placed after SL-1/SL-8/SL-9).
BUNDLE_ORDER = [
    "SV-0", "SV-1", "SV-2", "SV-3", "SV-4", "SV-5", "SL-7", "SL-4", "SL-2", "SL-3", "SL-5",
    "SL-6", "SL-10", "SL-1", "SL-8", "SL-9", "SL-11",
]  # fmt: skip

# Every row an A-1 label names -> the requirements row's own tier column, "+"-joined; a row whose
# cell splits by slice gives a Slice A label its Slice A component (I22, A1c2-7). Transcribed from
# requirements-workflow-runtime.md; L.J-SINGLE.1's RV-5 review diffs this map against the
# requirements.
ROW_TIER = {
    "WR-CANCEL-1": "PROC",  # venue BOTH
    "WR-CANCEL-3": "PROC",
    "WR-CANCEL-4": "PROC",  # PROC (A), DOCKER (B)
    "WR-CANCEL-5": "STUB+MCP",
    "WR-DEADLINE-2": "PROC",  # venue BOTH
    "WR-DEADLINE-3": "LOGIC",
    "WR-DEADLINE-4": "PROC",
    "WR-EVID-1": "PROC+MCP",
    "WR-EVID-3": "PROC",
    "WR-IDEM-3": "LOGIC",
    "WR-IDEM-5": "LOGIC",
    "WR-OWN-2": "PROC",  # PROC (A), DOCKER (B)
    "WR-OWN-6": "PROC",  # PROC+DOCKER
    "WR-OWN-7": "PROC",
    "WR-OWN-8": "PROC",
    "WR-PLAN-2": "MCP+LOGIC",
    "WR-PLAN-3": "PROC",
    "WR-PLAN-5": "PROC",
    "WR-PLAN-12": "LOGIC",
    "WR-PROOF-4": "STUB",  # STUB (DOCKER for the Docker adapter)
    "WR-PROOF-10": "INSPECT",
    "WR-REMEDY-1": "LOGIC",
    "WR-REMEDY-5": "LOGIC+MCP",
    "WR-TERM-1": "MCP",
    "WR-TERM-3": "MCP+PROC+STUB",
    "WR-TERM-4": "LOGIC",
    "WR-TERM-8": "MCP",
    "WR-TERM-9": "MCP",
    "WR-VERIFY-1": "LOGIC+PROC",
}

# Two deferred core clauses carry slice "core" (DM-19/DM-41); every other A-1 label is slice "A".
CORE_SLICE_LABELS = {
    "WR-CANCEL-3:later-run-admitted-after-answer",
    "WR-OWN-8:environment-lease",
}


def _single(cfg: fence_mod.FenceConfig):
    lanes = [lane for lane in cfg.lanes if lane.phase == "single"]
    gates = [gate for gate in cfg.gates if gate.phase == "single"]
    return lanes, gates


def test_fence_fragment_lanes_and_gates(tmp_path: Path) -> None:
    cfg = fence_mod.load_fence()
    lanes, gates = _single(cfg)
    assert lanes and gates

    # [phases].single lists exactly the single lanes' prefixes (the bundle prefix has its lane);
    # every gate sits under exactly one single lane (R4 reads it)
    lane_prefixes = {lane.branch_prefix for lane in lanes}
    assert lane_prefixes == set(cfg.phases["single"])
    for gate in gates:
        owners = [lane.name for lane in lanes if gate.branch.startswith(lane.branch_prefix)]
        assert len(owners) == 1, (gate.branch, owners)

    # every A-1 gate branch is a full branch name matched by equality only (CM-2), and matches only
    # gates of this fragment; the bundle branch carries the 17 product gates, which `match_gates`
    # lands in the table's order; J-SINGLE is alone on its checkpoint branch
    by_branch: dict[str, list[fence_mod.Gate]] = {}
    for gate in gates:
        assert not gate.branch.endswith("/"), gate.branch
        by_branch.setdefault(gate.branch, []).append(gate)
    assert set(by_branch) == {BUNDLE_BRANCH, "wr/j-single/J-SINGLE"}
    for branch, members in by_branch.items():
        matched = fence_mod.match_gates(branch, cfg.gates)
        assert [id(g) for g in matched] == [id(g) for g in members], branch
        assert all(g.phase == "single" for g in matched), branch
        if len(members) == 1:
            assert fence_mod.match_gate(branch, cfg.gates) is members[0]
    assert [g.merge for g in by_branch[BUNDLE_BRANCH]] == BUNDLE_ORDER
    assert len({g.merge for g in gates}) == len(gates)
    with pytest.raises(fence_mod.GateMatchError, match="use match_gates"):
        fence_mod.match_gate(BUNDLE_BRANCH, cfg.gates)
    assert [g.merge for g in by_branch["wr/j-single/J-SINGLE"]] == ["J-SINGLE"]

    # R4 judges every bundle chunk against the bundle lane: its globs hold every product lane's
    (bundle_lane,) = [lane for lane in lanes if lane.name == "single-bundle"]
    assert bundle_lane.branch_prefix == "wr/single-bundle/"
    product = [lane for lane in lanes if lane.name not in ("single-bundle", "j-single")]
    assert {lane.name for lane in product} == {
        "sv-docs", "sv-rec", "sv-con", "sv-host", "sv-runtime", "sl-r", "sl-h",
    }  # fmt: skip
    for lane in product:
        missing = [g for g in lane.globs if g not in bundle_lane.globs]
        assert not missing, (lane.name, missing)
    # paths the leaves need that no per-lane block owns (AM-4, AM-5, AM-10; core's deferral test)
    for extra in (
        "tests/proof/differ.py",
        "tests/proof/divergence.toml",
        "tests/proof/drift/core/test_sa05_clock.py",
        "tests/core/docs/test_cl_d1_deferrals.py",
    ):
        assert extra in bundle_lane.globs, extra

    # gate order encodes merge order: SV-0 needs J-CORE, every other predecessor is an earlier gate
    order = by_branch[BUNDLE_BRANCH]
    assert order[0].merge == "SV-0" and order[0].requires_merge == ["J-CORE"]
    seen: set[str] = {"J-CORE"}
    for gate in order:
        assert gate.requires_merge, gate.merge
        assert set(gate.requires_merge) <= seen, (gate.merge, gate.requires_merge)
        seen.add(gate.merge)
        if gate.merge.startswith("SL-"):
            assert "SV-5" in gate.requires_merge, gate.merge
    by_merge = {g.merge: g for g in gates}
    assert by_merge["J-SINGLE"].requires_merge == ["SL-11"]

    # no A-1 lane glob reaches a `leave` glob (SA-11), and a lane globs only the A-1 fence fragment
    for lane in lanes:
        for g in lane.globs:
            if g.startswith("!"):
                continue
            assert not fence_mod.glob_match(g.rstrip("*").rstrip("/"), cfg.leave), (lane.name, g)
            if g.startswith("tests/proof/fence.d/"):
                assert g == "tests/proof/fence.d/single.toml", (lane.name, g)

    # the checkpoint lane holds only the J-SINGLE gate, its globs exactly CM-5's
    (ckpt,) = [lane for lane in lanes if lane.name == "j-single"]
    assert sorted(ckpt.globs) == [
        "tests/fixtures/fossils/single/**",
        "tests/proof/reviews/*-single.toml",
    ]
    ckpt_gates = [g for g in gates if g.branch.startswith(ckpt.branch_prefix)]
    assert [g.merge for g in ckpt_gates] == ["J-SINGLE"]
    assert ckpt_gates[0].branch == "wr/j-single/J-SINGLE"

    # the schema is closed: a planted withdrawn key fails the load
    frag = tmp_path / "fence.d"
    frag.mkdir()
    (frag / "single.toml").write_text(
        'phase = "single"\n[[lane]]\nname = "x"\nbranch_prefix = "wr/x/"\n'
        'globs = ["a"]\nhot = ["a"]\n'
    )
    with pytest.raises(fence_mod.FenceLoadError, match="withdrawn"):
        fence_mod.load_fence(d_dir=frag)


def _single_labels() -> list[dict]:
    return list(tomllib.loads((PROOF / "labels.d" / "single.toml").read_text())["label"])


def test_labels_rows_exist_and_postures_valid() -> None:
    labels = _single_labels()
    assert len(labels) == 53
    rows = {r["id"] for r in tomllib.loads(meta.ROW_OWNERS_PATH.read_text())["row"]}
    oqs = {o["id"] for o in meta.load_open_questions()}
    for label in labels:
        lid = label["id"]
        assert set(label) <= meta.CSC1_ALL_KEYS, lid
        assert meta.CSC1_REQUIRED_KEYS <= set(label), lid
        assert label["posture"] in meta.CSC1_POSTURES, lid
        assert lid.split(":", 1)[0] == label["row"], lid
        assert label["row"] in rows, lid
        assert label["step"] == "single", lid
        assert label["slice"] == ("core" if lid in CORE_SLICE_LABELS else "A"), lid
        assert label["venue"] in ("CI", "HOST", "BOTH"), lid
        assert LEAF_ID.match(label["declared_by"]), lid
        # A-1 declares no HOST-venue label (CSC-9); the tier is the row's own tier (A1c2-7)
        assert label["venue"] != "HOST", lid
        assert label["tier"] == ROW_TIER[label["row"]], (lid, label["tier"])
        # `oq` present iff posture in {gated_on, both_variant}, and listed in open_questions.toml
        if label["posture"] in ("gated_on", "both_variant"):
            assert label["oq"] in oqs, lid
        else:
            assert "oq" not in label, lid
        assert label.get("oq") != "OQ-32", lid  # OQ-32 answered 2026-09-28
        # `reason` present iff posture = na
        assert ("reason" in label) == (label["posture"] == "na"), lid
    assert {"WR-TERM-3:no-progress-class-failed"}.isdisjoint(label["id"] for label in labels)
    assert set(ROW_TIER) == {label["row"] for label in labels}

    # every label id is unique across all labels.d fragments, and the real registry loads
    ids = [str(label["id"]) for label in meta._load_all_labels()]
    assert len(ids) == len(set(ids)), sorted({i for i in ids if ids.count(i) > 1})
    single_ids = {label["id"] for label in labels}
    assert single_ids <= set(ids)
    from argparse import Namespace

    assert meta.cmd_audit_rows(Namespace()) == 0


def test_temporary_header() -> None:
    path = PROOF / "temporary.d" / "single.toml"
    text = path.read_text()
    # a comment header naming the CM-7 schema; the five introducing leaves append the entries
    header = text.split("[[entry]]", 1)[0]
    assert header.lstrip().startswith("# tests/proof/temporary.d/single.toml")
    assert all(not line.strip() or line.startswith("#") for line in header.splitlines())
    assert "CM-7" in header
    introducers = {"L.SV-3.4", "L.SV-3.5", "L.SV-3.7", "L.SV-5.7", "L.SV-5.9"}
    for entry in tomllib.loads(text).get("entry", []):
        assert entry["introduced_by"] in introducers, entry["id"]
        assert entry["permanent"] is False, entry["id"]
    # the fragment loads under the register's loader alongside every other fragment
    register.load_entries()
