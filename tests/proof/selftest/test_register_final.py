"""Selftest for `meta register --final` (L.CZ.5; CM-7, SC-5). Planted register files and entries
only. The live command `python -m tests.proof.meta register --final` exits 0 at the CZ PR head,
where every removal has landed."""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path

import pytest

from tests.proof import meta as meta_mod
from tests.proof import register as register_mod

GOOD_BOUNDARY = {"merge": "CS-1", "class": "forward_only", "evidence": "t.py::test_x"}


def _entry(eid: str, probe: str, removed_by: str = "L.A.2", **kw) -> dict:
    return {
        "id": eid,
        "mechanism": "m",
        "introduced_by": "S0",
        "serves": [],
        "probe": probe,
        "removal_condition": "c",
        "removed_by": removed_by,
        "permanent": False,
        **kw,
    }


def _command(monkeypatch, entries, boundaries=(GOOD_BOUNDARY,)) -> int:
    monkeypatch.setattr(register_mod, "load_entries", lambda **kw: list(entries))
    monkeypatch.setattr(register_mod, "load_rollback", lambda **kw: list(boundaries))
    return meta_mod.cmd_register(Namespace(final=True, probe=None))


def test_planted_present_probe_fails_final(monkeypatch, capsys):
    gone = _entry("X-GONE", "false")
    present = _entry("X-PRESENT", "true", removed_by="L.CZ.5-planted")
    assert _command(monkeypatch, [gone]) == 0
    assert _command(monkeypatch, [gone, present]) == 1
    assert "X-PRESENT is present and its remover is L.CZ.5-planted" in capsys.readouterr().out
    # a present entry that is still `serves`-ing a clause is the same failure, by its probe alone
    serving = _entry("X-SERVING", "true", serves=["B2.2"])
    assert meta_mod.register_final_problems([serving], [GOOD_BOUNDARY]) == [
        "X-SERVING is present and its remover is L.A.2"
    ]


def test_named_not_removed_with_citation_accepted(monkeypatch):
    kept = _entry(
        "TM-X-KEPT",
        "true",  # the mechanism is still there, and named as staying
        removed_by="named-not-removed",
        citation="D-19: real Gradle is out of scope",
    )
    assert meta_mod.register_final_problems([kept], [GOOD_BOUNDARY]) == []
    assert _command(monkeypatch, [kept, _entry("X-GONE", "false")]) == 0


def test_named_not_removed_entry_serving_a_clause_rejected(tmp_path: Path, monkeypatch, capsys):
    serving = _entry(
        "TM-X-SERVING",
        "true",
        removed_by="named-not-removed",
        citation="a citation",
        serves=["B2.2"],
    )
    assert meta_mod.register_final_problems([serving], []) == [
        "TM-X-SERVING: named-not-removed but serves ['B2.2']"
    ]
    uncited = _entry("TM-X-UNCITED", "true", removed_by="named-not-removed")
    assert meta_mod.register_final_problems([uncited], []) == [
        "TM-X-UNCITED: named-not-removed without a citation"
    ]
    permanent = _entry("TM-X-PERMANENT", "false", permanent=True)
    assert "permanent infrastructure is not registered" in " ".join(
        meta_mod.register_final_problems([permanent], [])
    )

    # the loader refuses both shapes before `--final` ever runs (CM-7), and the command exits 1
    base = tmp_path / "temporary.toml"
    base.write_text(
        '[[entry]]\nid = "TM-X-SERVING"\nmechanism = "m"\nintroduced_by = "S0"\n'
        'serves = ["B2.2"]\nprobe = "true"\nremoval_condition = "c"\n'
        'removed_by = "named-not-removed"\npermanent = false\ncitation = "c"\n'
    )
    with pytest.raises(register_mod.RegisterLoadError):
        register_mod.load_entries(temporary_path=base, d_dir=tmp_path / "none")
    monkeypatch.setattr(register_mod, "TEMPORARY_PATH", base)
    monkeypatch.setattr(register_mod, "TEMPORARY_D_DIR", tmp_path / "none")
    assert meta_mod.cmd_register(Namespace(final=True, probe=None)) == 1
    assert "named-not-removed requires serves = []" in capsys.readouterr().out


def test_final_records_every_rollback_boundarys_class(monkeypatch):
    no_class = {"merge": "SV-3", "evidence": "t.py::test_y"}
    bad_class = {"merge": "TR-L", "class": "reversible", "evidence": "t.py::test_z"}
    pending = {"merge": "TR-5", "class": "transparent", "evidence": "pending:TR-5"}
    found = meta_mod.register_final_problems([], [no_class, bad_class, pending, GOOD_BOUNDARY])
    assert found == [
        "rollback SV-3: class None is not recorded",
        "rollback TR-L: class 'reversible' is not recorded",
        "rollback TR-5: evidence is still 'pending:TR-5'",
    ]
    assert _command(monkeypatch, [], [GOOD_BOUNDARY]) == 0
    assert _command(monkeypatch, [], [no_class]) == 1


def test_the_committed_register_holds_no_permanent_entry_and_every_boundary_has_a_class():
    for entry in register_mod.load_entries():
        assert entry["permanent"] is False
        assert isinstance(entry["removed_by"], str)
    boundaries = register_mod.load_rollback()
    assert boundaries and all(b["class"] in meta_mod.MC31_CLASSES for b in boundaries)
