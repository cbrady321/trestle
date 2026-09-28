"""Selftest for L.P0-0c.3: k_doc_map.toml, open_questions.toml, and
`meta open-questions`."""

from __future__ import annotations

from argparse import Namespace

import pytest

from tests.proof import meta

EXPECTED_OQ_IDS = [
    "OQ-25",
    "OQ-26",
    "OQ-27",
    "OQ-29",
    "OQ-30",
    "OQ-31",
    "F-13(d)",
    "F-11(a)",
    "F-11(b)",
    "F-12",
    "F-B3-2",
    "OPEN-MISE-HOST",
]

REQUIRED_K_FIELDS = {
    "id",
    "rows",
    "docs",
    "docs_source",
    "confirm",
    "recorded_default",
    "landing_merge",
    "must_appear_by",
}

ANSWERED_OR_WITHDRAWN = ["OQ-32", "Q-STRADDLE-ORPHAN", "PX-4", "PX-1", "PX-2"]


def test_19_k_items_complete() -> None:
    ks = tomllib_k()
    assert [k["id"] for k in ks] == [f"K-{i}" for i in range(1, 20)]
    for k in ks:
        missing = REQUIRED_K_FIELDS - set(k)
        assert not missing, f"{k['id']} missing {missing}"


def tomllib_k() -> list[dict[str, object]]:
    import tomllib

    return list(tomllib.loads(meta.K_DOC_MAP_PATH.read_text()).get("k", []))


def test_open_questions_ids_exact() -> None:
    oqs = meta.load_open_questions()
    assert [o["id"] for o in oqs] == EXPECTED_OQ_IDS


def test_oq_postures() -> None:
    oqs = meta.load_open_questions()
    for o in oqs:
        assert o["posture"] in ("gated_on", "both_variant", "neutral")
        assert o["kind"] == "maintainer_question"


def test_answered_or_withdrawn_id_is_not_an_open_question() -> None:
    oq_ids = {o["id"] for o in meta.load_open_questions()}
    for answered in ANSWERED_OR_WITHDRAWN:
        assert answered not in oq_ids


def test_planted_decided_oq_clause_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    # A label whose oq names an id outside open_questions.toml is a load error.
    bad_unknown_oq = [
        {
            "id": "X:y",
            "row": "X",
            "step": "core",
            "slice": "core",
            "tier": "must",
            "venue": "CI",
            "posture": "gated_on",
            "oq": "OQ-32",
            "declared_by": "test",
        }
    ]
    monkeypatch.setattr(meta, "_load_all_labels", lambda: bad_unknown_oq)
    assert meta.cmd_open_questions(Namespace()) == 1

    # A proves marker/label that decides (posture=proven) an OQ-bound clause fails.
    bad_decided = [
        {
            "id": "X:y",
            "row": "X",
            "step": "core",
            "slice": "core",
            "tier": "must",
            "venue": "CI",
            "posture": "proven",
            "oq": "OQ-30",
            "declared_by": "test",
        }
    ]
    monkeypatch.setattr(meta, "_load_all_labels", lambda: bad_decided)
    assert meta.cmd_open_questions(Namespace()) == 1

    good = [dict(bad_decided[0], posture="both_variant")]
    monkeypatch.setattr(meta, "_load_all_labels", lambda: good)
    assert meta.cmd_open_questions(Namespace()) == 0
