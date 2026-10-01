"""L.RB-0.5: MC-B-05, the STUB-PROVEN label registry (`stub_labels.toml`).

The live test reads the committed file against every declared label; the planted tests pin each
rule on synthetic labels. A B-PRE leaf declares a stub label before the leaf that appends its row
lands (AMB-8), so a row is required only once its row leaf's commit is in the history.
"""

from __future__ import annotations

import copy
import tomllib

import pytest

from tests.proof import meta as meta_mod
from tests.proof.b import stub_labels as sl

SUFFIX = sl.TWIN_SUFFIX
ROW = {
    "label": "WR-ENV-3:allowlisted-noninteractive-identity",
    "group": "D-1",
    "text": "the stub stands for mise; the real tool's behaviour is unverified",
    "deferral": "OPEN-MISE-HOST",
}
LABELS = [
    {
        "id": ROW["label"],
        "posture": "stub_proven",
        "declared_by": "L.RB-4.3",
    },
    {"id": ROW["label"] + SUFFIX, "posture": "stub_proven", "declared_by": "L.RB-4.3"},
    {"id": "WR-ENV-1:claim", "posture": "claim", "declared_by": "L.RB-1.1"},
]


def _stub(*rows: dict) -> sl.StubLabels:
    return sl.StubLabels(twin_suffix=SUFFIX, rows=[dict(r) for r in rows])


@pytest.mark.proves("WR-PROOF-3", "WR-PROOF-3:b-stub-labels-registered", "B", "B", "LOGIC", "CI")
def test_committed_stub_labels_are_consistent_with_the_declared_labels():
    stub = sl.load()
    labels = list(meta_mod._load_all_labels())  # noqa: SLF001
    assert sl.problems(stub, labels, sl.landed_leaves()) == []


def test_twin_suffix_is_the_csc8_spelling_and_no_other_key_exists():
    raw = tomllib.loads(sl.STUB_LABELS_PATH.read_text())
    assert raw["twin_suffix"] == "@stub-twin"
    assert set(raw) <= sl.TOP_KEYS


def test_planted_complete_registry_is_clean():
    assert sl.problems(_stub(ROW), LABELS, landed={"L.RB-4.5"}) == []


def test_planted_twin_suffix_change_rejected():
    stub = sl.StubLabels(twin_suffix="@twin", rows=[dict(ROW)])
    assert any("twin_suffix" in p for p in sl.problems(stub, LABELS, landed={"L.RB-4.5"}))


def test_planted_stub_proven_label_without_row_rejected_once_row_leaf_landed():
    problems = sl.problems(_stub(), LABELS, landed={"L.RB-4.5"})
    assert any(ROW["label"] in p and "no row" in p for p in problems)


def test_planted_missing_row_waits_for_its_row_leaf():
    # AMB-8: L.RB-4.3 declares the label; L.RB-4.5 appends the row and has not landed
    assert sl.problems(_stub(), LABELS, landed={"L.RB-4.3"}) == []
    # a history that cannot be read reads strictly
    assert sl.problems(_stub(), LABELS, landed=None) != []


def test_planted_label_whose_declarer_has_no_row_leaf_is_always_required():
    labels = [{"id": "WR-X:y", "posture": "stub_proven", "declared_by": "L.RB-6.1"}]
    assert any("WR-X:y" in p for p in sl.problems(_stub(), labels, landed=set()))


def test_planted_row_for_undeclared_or_non_stub_proven_label_rejected():
    assert any(
        "no such label" in p for p in sl.problems(_stub(dict(ROW, label="NOPE")), LABELS, set())
    )
    claim = dict(ROW, label="WR-ENV-1:claim")
    assert any("posture" in p for p in sl.problems(_stub(claim), LABELS, {"L.RB-4.5"}))


def test_planted_row_for_a_twin_label_rejected():
    twin = dict(ROW, label=ROW["label"] + SUFFIX)
    assert any("twin label has no row" in p for p in sl.problems(_stub(twin), LABELS, {"L.RB-4.5"}))


def test_planted_duplicate_row_rejected():
    assert any("2 times" in p for p in sl.problems(_stub(ROW, ROW), LABELS, {"L.RB-4.5"}))


@pytest.mark.parametrize("group", ["B4.5", "B9.1", "D-99", ""])
def test_planted_group_outside_mc_b_05_rejected_b45_and_b91_carry_no_stub_label(group):
    assert any(
        "group" in p for p in sl.problems(_stub(dict(ROW, group=group)), LABELS, {"L.RB-4.5"})
    )


@pytest.mark.parametrize("key", ["text", "deferral"])
def test_planted_empty_row_text_or_deferral_rejected(key):
    assert any(
        f"{key} is empty" in p
        for p in sl.problems(_stub(dict(ROW, **{key: " "})), LABELS, {"L.RB-4.5"})
    )


def test_planted_bad_row_schema_rejected():
    row = copy.deepcopy(ROW)
    row["extra"] = "x"
    del row["text"]
    assert any("bad schema" in p for p in sl.problems(_stub(row), LABELS, {"L.RB-4.5"}))


def test_row_leaf_mapping_follows_the_merge_of_declared_by():
    assert sl.row_leaf({"declared_by": "L.RB-4.2"}) == "L.RB-4.5"
    assert sl.row_leaf({"declared_by": "L.RB-7.2"}) == "L.RB-7.2"
    assert sl.row_leaf({"declared_by": "L.RB-9.3"}) == "L.RB-9.6"
    assert sl.row_leaf({"declared_by": "L.RB-10.2"}) == "L.RB-10.2"
    assert sl.row_leaf({"declared_by": "L.NW-2.3"}) is None


def test_docs_state_real_tool_unverified():
    """RV-2 reads this at J-SLICE-B: docs/environment.md says the toolchain leg is STUB-PROVEN and
    that the real mise (OPEN-MISE-HOST) and Gradle (D-19) are unverified."""
    text = (sl.ROOT / "docs" / "environment.md").read_text()
    assert "STUB-PROVEN" in text and "unverified" in text
    assert "OPEN-MISE-HOST" in text and "D-19" in text and "OQ-26" in text


def test_docs_name_the_build_daemon_boundary_build_daemon_boundary():
    """RV-1 presence (L.RB-10.2; WR-CANCEL-6, D-3): `docs/environment.md` names the escaping helper
    of a build as a boundary that is not contained, disclosed and unproven pending D-19."""
    text = (sl.ROOT / "docs" / "environment.md").read_text(encoding="utf-8")
    assert "Containment boundary" in text
    boundary = text.split("Containment boundary", 1)[1].lower()
    for phrase in ("outside gradle's daemon", "not contained", "disclosed", "d-19"):
        assert phrase in boundary, phrase
