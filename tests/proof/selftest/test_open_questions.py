"""Selftest for `meta open-questions --final` (L.CZ.7; C.9, CSC-15, DM-87). Planted labels,
open questions and deferrals only; the live command is accepted at the CZ PR head."""

from __future__ import annotations

from argparse import Namespace

import pytest

from tests.proof import meta as meta_mod

IDS = sorted(meta_mod.OQ_FINAL_IDS)


def _oqs(ids=IDS, posture="gated_on") -> list[dict]:
    return [{"id": i, "kind": "maintainer_question", "posture": posture, "where": "w"} for i in ids]


def _label(label_id: str, **kw) -> dict:
    return {
        "id": label_id,
        "row": "WR-X-1",
        "step": "core",
        "slice": "core",
        "tier": "PROC",
        "venue": "CI",
        "posture": "gated_on",
        "declared_by": "test",
        **kw,
    }


def test_the_twelve_ids_are_c9s_and_the_answered_two_are_absent() -> None:
    assert len(IDS) == 12
    assert {"OQ-25", "OQ-26", "OQ-27", "OQ-29", "OQ-30", "OQ-31"} < set(IDS)
    assert {"F-13(d)", "F-11(a)", "F-11(b)", "F-12", "F-B3-2", "OPEN-MISE-HOST"} < set(IDS)
    assert not set(meta_mod.OQ_ANSWERED) & set(IDS)
    assert meta_mod.open_question_problems([], _oqs(), []) == []
    # neutral and both_variant are postures too
    assert meta_mod.open_question_problems([], _oqs(posture="neutral"), []) == []
    assert meta_mod.open_question_problems([], _oqs(posture="both_variant"), []) == []


def test_planted_claimed_gated_label_fails(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    claimed = [_label("WR-X-1:claims-oq", oq="OQ-25", posture="claim")]
    problems = meta_mod.open_question_problems(claimed, _oqs(), [])
    assert problems == [
        "WR-X-1:claims-oq: is bound to OQ-25 but posture 'claim' claims it "
        "(must be gated_on or both_variant)"
    ]
    # the command fails on it (the report-mode check already does), and passes once gated
    monkeypatch.setattr(meta_mod, "load_open_questions", lambda: _oqs())
    monkeypatch.setattr(meta_mod, "_load_all_labels", lambda: claimed)
    assert meta_mod.cmd_open_questions(Namespace(final=True)) == 1
    monkeypatch.setattr(
        meta_mod, "_load_all_labels", lambda: [dict(claimed[0], posture="gated_on")]
    )
    monkeypatch.setattr("tests.proof.deferrals.load_deferrals", lambda *a, **kw: [])
    assert meta_mod.cmd_open_questions(Namespace(final=True)) == 0
    capsys.readouterr()


def test_planted_label_without_oq_fails() -> None:
    # a label that says it is gated on a question, with no question named, is not gated
    bare = _label("WR-X-1:gated-with-nothing", posture="gated_on")
    assert meta_mod.label_problems(meta_mod.EnforceWorld("ci"), bare) == [
        "WR-X-1:gated-with-nothing is gated_on with no oq"
    ]
    # and a label naming an id that is not one of the twelve is refused by `--final`
    unknown = _label("WR-X-1:unknown-oq", oq="OQ-99")
    problems = meta_mod.open_question_problems([unknown], _oqs(), [])
    assert problems == ["WR-X-1:unknown-oq: oq='OQ-99' is not an open question"]
    # `open_questions.toml` itself must carry exactly C.9's twelve
    assert meta_mod.open_question_problems([], _oqs(IDS[:-1]), []) == [
        f"{IDS[-1]} is not in open_questions.toml"
    ]
    assert meta_mod.open_question_problems([], _oqs(IDS + ["OQ-99"]), []) == [
        "OQ-99 is in open_questions.toml but is not one of C.9's twelve"
    ]


def test_planted_oq_naming_answered_question_fails() -> None:
    for answered in meta_mod.OQ_ANSWERED:
        labels = [_label("WR-X-1:answered", oq=answered)]
        problems = meta_mod.open_question_problems(labels, _oqs(), [])
        assert problems == [
            f"WR-X-1:answered: oq={answered!r} names a question the maintainer answered"
        ]
        # nor may the answered question sit in open_questions.toml
        listed = meta_mod.open_question_problems([], _oqs(IDS + [answered]), [])
        assert f"{answered} is answered, yet is in open_questions.toml" in listed


def test_a_px_bound_deferral_fails() -> None:
    deferrals = [
        {"label": "WR-X-1:ok", "from_step": "core", "closes_at": "CZ"},
        {"label": "WR-X-2:px", "from_step": "core", "closes_at": "CZ", "until": "PX-3"},
    ]
    assert meta_mod.open_question_problems([], _oqs(), deferrals) == [
        "WR-X-2:px: a PX-bound deferral (until='PX-3')"
    ]


def test_final_holds_on_the_committed_registry() -> None:
    from tests.proof import deferrals as deferrals_mod

    problems = meta_mod.open_question_problems(
        meta_mod._load_all_labels(), meta_mod.load_open_questions(), deferrals_mod.load_deferrals()
    )
    assert problems == []
