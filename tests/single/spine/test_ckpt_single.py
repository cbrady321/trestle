"""L.SV-5.14: planted self-tests of `tests/proof/ckpt/single.py` (CM-5, MC-28).

These are the only default-collected checkpoint tests: each is true on every later head. They import
the condition module (which runs nothing: each condition is data) and exercise its pure functions
over synthetic values; the live nodes are `tests/proof/ckpt/single_conditions.py`, never default-
collected (DM-80), and run only under `meta ckpt single`."""

from __future__ import annotations

import importlib
import subprocess
from pathlib import Path
from typing import Any

import pytest

from tests.proof import ckpt as ckpt_mod
from tests.proof import deferrals as deferrals_mod
from tests.proof import transcribe as transcribe_mod
from tests.proof.ckpt import single

LETTERS = "abcdef"


def _deferral(label: str, closes_at: str) -> dict[str, Any]:
    return {
        "label": label,
        "from_step": "core",
        "closes_at": closes_at,
        "citation": "planted",
        "declared_by": "planted",
    }


def test_module_declares_a_to_f() -> None:
    assert single.TRIGGER_MERGE == "J-SINGLE"
    assert single.TAG == "wr-ckpt/single"
    assert [c.id for c in single.CONDITIONS] == [f"J-SINGLE-{x}" for x in LETTERS]
    assert list(single.SPEC) == list(LETTERS)
    assert all(isinstance(c, ckpt_mod.Condition) and not c.merge_only for c in single.CONDITIONS)
    module = ckpt_mod.load_module("single")
    assert module.TRIGGER_MERGE == "J-SINGLE" and module.TAG == "wr-ckpt/single"
    # every condition names the explicit node id or command that evaluates it (declared as data)
    for letter, spec in single.SPEC.items():
        assert spec.id == f"J-SINGLE-{letter}"
        assert spec.title
        named = [" ".join(cmd.argv) for cmd in spec.commands]
        assert named or spec.extra is not None, letter
    node_a = " ".join(single.SPEC["a"].commands[0].argv)
    assert "tests/proof/ckpt/single_conditions.py::test_condition_a" in node_a
    audited = " ".join(single.SPEC["b"].commands[0].argv)
    assert "-p tests.proof.ckpt.single_vertex_audit" in audited
    assert "tests/single tests/proof/spine tests/proof/suites" in audited


def test_importing_the_module_runs_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Conditions are data: a reload spawns no process, however the module is imported."""

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("importing tests.proof.ckpt.single ran a command")

    monkeypatch.setattr(subprocess, "run", refuse)
    monkeypatch.setattr(subprocess, "Popen", refuse)
    reloaded = importlib.reload(single)
    assert [c.id for c in reloaded.CONDITIONS] == [f"J-SINGLE-{x}" for x in LETTERS]


def test_condition_c_closing_set_from_deferrals(tmp_path: Path) -> None:
    """Condition c applies CM-8's band rule: every deferral in the (planted) deferrals file whose
    `closes_at` is an A-1 merge has its label PROVEN at the candidate."""
    merges = single.a1_merges()
    assert {"SV-5", "SL-8", "SL-9", "SL-11"} <= merges and "J-SINGLE" not in merges
    planted = tmp_path / "deferrals.toml"
    planted.write_text(
        "\n".join(
            f'[[deferral]]\nlabel = "{d["label"]}"\nfrom_step = "core"\n'
            f'closes_at = "{d["closes_at"]}"\ncitation = "planted"\ndeclared_by = "planted"\n'
            for d in (
                _deferral("WR-OWN-8:environment-lease", "SL-8"),
                _deferral("A1.3:single", "SL-9"),
                _deferral("WR-OWN-1:created-container-released", "RB-12"),
                _deferral("WR-TERM-5:tree-size", "TR-6"),
            )
        ),
        encoding="utf-8",
    )
    entries = deferrals_mod.load_deferrals(planted)
    assert single.closing_set(entries, merges) == ["A1.3:single", "WR-OWN-8:environment-lease"]
    # a deferral closing at an A-1 merge whose label is unproven fails; the band's later
    # (B, tree, close-out) deferrals are not this checkpoint's
    problems = single.closing_violations(entries, merges, {"A1.3:single"})
    assert len(problems) == 1 and "WR-OWN-8:environment-lease" in problems[0]
    assert "closes_at='SL-8'" in problems[0]
    # and passes when every label of the closing set is PROVEN (an unrelated one is not needed)
    assert (
        single.closing_violations(entries, merges, {"A1.3:single", "WR-OWN-8:environment-lease"})
        == []
    )
    # nothing closes at an A-1 merge: nothing is required
    assert single.closing_violations(entries, {"NOWHERE"}, set()) == []


def test_closing_set_of_the_shipped_deferrals_is_the_bands_three_labels() -> None:
    entries = deferrals_mod.load_deferrals()
    assert single.closing_set(entries, single.a1_merges()) == sorted(single.BAND_LABELS)


def test_clause_parts_are_the_eighteen_single_and_twelve_tree() -> None:
    clauses = list(transcribe_mod.load_matrix_map())
    assert len(single.single_parts(clauses)) == 18
    assert len(single.tree_parts(clauses)) == 12
    assert set(single.BOTH_PARTS) <= set(single.single_parts(clauses))
    assert not set(single.single_parts(clauses)) & set(single.tree_parts(clauses))


def test_part_problems_over_a_synthetic_ledger() -> None:
    def rec(label: str, **over: Any) -> dict[str, Any]:
        base = {
            "labels": [label],
            "gate": "ci-test",
            "venue": "CI",
            "outcome": "passed",
            "interpreter": "3.12.8",
        }
        return {**base, **over}

    report = {
        "A1.2": {"status": "PROVEN"},
        "A2.1": {"status": "PROVEN"},
        "A2.3": {"status": "UNPROVEN"},
    }
    records = [rec("A1.2"), rec("A2.1", gate="ci-lane")]
    problems = single.part_problems(report, records, ["A1.2", "A2.1", "A2.3", "A2.4"])
    assert len(problems) == 3
    assert any("A2.1 is not PROVEN@CI through a named gate" in p for p in problems)
    assert any("A2.3 is not PROVEN in the ledger" in p for p in problems)
    assert any("A2.4 is not PROVEN in the ledger" in p for p in problems)
    # a venue-BOTH part needs the host-proc record, resolved by the caller
    both = single.part_problems(report, records, ["A1.2"], both=["A1.2"], host_record=None)
    assert len(both) == 1 and "no admissible host-proc record" in both[0]
    host = {"results": [{"labels": ["A1.2"], "outcome": "PASSED"}]}
    assert single.part_problems(report, records, ["A1.2"], both=["A1.2"], host_record=host) == []
    failing = {"results": [{"labels": ["A1.2"], "outcome": "FAILED"}]}
    assert single.part_problems(report, records, ["A1.2"], both=["A1.2"], host_record=failing)
    # a tree clause that anything claims is reported
    assert single.unclaimed_problems({"A1.5": {"status": "UNPROVEN"}}, ["A1.5", "A1.6"]) == [
        "tree clause A1.5 is claimed (UNPROVEN)"
    ]
