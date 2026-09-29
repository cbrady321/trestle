"""Selftest for the temporary register (CM-7; L.P0-0d.1)."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from tests.proof import register as register_mod

ROOT = Path(__file__).resolve().parents[3]


def _write(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


@pytest.fixture(autouse=True)
def _no_live_collection(monkeypatch):
    """Selftests plant labels; never collect the live suite for registrants
    (the default: no registrant, i.e. a claim with nothing red behind it)."""
    monkeypatch.setattr(register_mod, "_label_registrants", lambda: {})


def test_every_entry_has_probe_condition_owner():
    for entry in register_mod.load_entries():
        assert entry["probe"]
        assert entry["removal_condition"]
        assert entry["removed_by"]


def test_removed_by_single_leaf_or_named_not_removed():
    for entry in register_mod.load_entries():
        rb = entry["removed_by"]
        assert isinstance(rb, str)
        assert rb == "named-not-removed" or rb.startswith("L.")


def test_permanent_true_rejected(tmp_path):
    base = _write(
        tmp_path,
        "temporary.toml",
        """
[[entry]]
id = "X-1"
mechanism = "m"
introduced_by = "L.A.1"
serves = []
probe = "true"
removal_condition = "c"
removed_by = "L.A.2"
permanent = true
""",
    )
    d_dir = tmp_path / "temporary.d"
    d_dir.mkdir()
    with pytest.raises(register_mod.RegisterLoadError):
        register_mod.load_entries(temporary_path=base, d_dir=d_dir)


def test_planted_removed_by_list_is_load_error(tmp_path):
    base = _write(
        tmp_path,
        "temporary.toml",
        """
[[entry]]
id = "X-1"
mechanism = "m"
introduced_by = "L.A.1"
serves = []
probe = "true"
removal_condition = "c"
removed_by = ["L.A.2", "L.A.3"]
permanent = false
""",
    )
    d_dir = tmp_path / "temporary.d"
    d_dir.mkdir()
    with pytest.raises(register_mod.RegisterLoadError):
        register_mod.load_entries(temporary_path=base, d_dir=d_dir)


def test_planted_green_clause_with_present_mechanism_fails(monkeypatch):
    entries = [
        {
            "id": "X-1",
            "mechanism": "m",
            "introduced_by": "L.A.1",
            "serves": ["A1.1:core"],
            "probe": "true",
            "removal_condition": "c",
            "removed_by": "L.A.2",
            "permanent": False,
        }
    ]
    monkeypatch.setattr(
        register_mod,
        "_load_all_labels",
        lambda: [{"id": "lbl-1", "row": "A1.1:core", "posture": "claim"}],
    )
    violations = register_mod.register_violations(entries)
    assert violations == ["lbl-1"]


def test_multi_phase_entry_serves_only_active_phase(tmp_path, monkeypatch):
    entries = [
        {
            "id": "X-1",
            "mechanism": "m",
            "introduced_by": "L.A.1",
            "serves": [],
            "phases": [
                {"name": "full", "serves": ["A1.5"], "probe": "false"},
                {"name": "choice-only", "serves": ["A2.6"], "probe": "true"},
            ],
            "probe": "true",
            "removal_condition": "c",
            "removed_by": "L.A.2",
            "permanent": False,
        }
    ]
    monkeypatch.setattr(
        register_mod,
        "_load_all_labels",
        lambda: [{"id": "lbl-full", "row": "A1.5", "posture": "claim"}],
    )
    assert register_mod.register_violations(entries) == []
    monkeypatch.setattr(
        register_mod,
        "_load_all_labels",
        lambda: [{"id": "lbl-choice", "row": "A2.6", "posture": "claim"}],
    )
    assert register_mod.register_violations(entries) == ["lbl-choice"]


def test_fragments_merged(tmp_path):
    base = _write(
        tmp_path,
        "temporary.toml",
        """
[[entry]]
id = "X-1"
mechanism = "m"
introduced_by = "L.A.1"
serves = []
probe = "true"
removal_condition = "c"
removed_by = "L.A.2"
permanent = false
""",
    )
    d_dir = tmp_path / "temporary.d"
    d_dir.mkdir()
    _write(
        tmp_path,
        "temporary.d/phaseb.toml",
        """
[[entry]]
id = "X-2"
mechanism = "m2"
introduced_by = "L.B.1"
serves = []
probe = "true"
removal_condition = "c"
removed_by = "L.B.2"
permanent = false
""",
    )
    entries = register_mod.load_entries(temporary_path=base, d_dir=d_dir)
    assert {e["id"] for e in entries} == {"X-1", "X-2"}


def test_probes_execute():
    assert register_mod._run_probe("true") is True
    assert register_mod._run_probe("false") is False


def test_presence_is_exit_status_not_output():
    # A probe that prints "present" but exits 1 reads absent.
    assert register_mod._run_probe("echo present; exit 1") is False


def test_recursive_probe_rejected(tmp_path):
    base = _write(
        tmp_path,
        "temporary.toml",
        """
[[entry]]
id = "X-1"
mechanism = "m"
introduced_by = "L.A.1"
serves = []
probe = "python -m tests.proof.meta register"
removal_condition = "c"
removed_by = "L.A.2"
permanent = false
""",
    )
    d_dir = tmp_path / "temporary.d"
    d_dir.mkdir()
    with pytest.raises(register_mod.RegisterLoadError):
        register_mod.load_entries(temporary_path=base, d_dir=d_dir)


def test_serves_through_composes(monkeypatch):
    entries = [
        {
            "id": "X-1",
            "mechanism": "m",
            "introduced_by": "L.A.1",
            "serves": ["A1.1:core"],
            "probe": "true",
            "removal_condition": "c",
            "removed_by": "L.A.2",
            "permanent": False,
        }
    ]
    monkeypatch.setattr(
        register_mod,
        "_load_all_labels",
        lambda: [
            {"id": "lbl-composed", "row": "row-x", "composes": "A1.1:core", "posture": "claim"}
        ],
    )
    assert register_mod.register_violations(entries) == ["lbl-composed"]


def test_probe_cli_prints_active_phase(capsys):
    entries = [
        {
            "id": "X-1",
            "mechanism": "m",
            "introduced_by": "L.A.1",
            "serves": [],
            "phases": [
                {"name": "full", "serves": [], "probe": "false"},
                {"name": "choice-only", "serves": [], "probe": "true"},
            ],
            "probe": "true",
            "removal_condition": "c",
            "removed_by": "L.A.2",
            "permanent": False,
        }
    ]
    import tests.proof.register as reg

    orig = reg.load_entries
    reg.load_entries = lambda **kw: entries
    try:
        rc = reg.cmd_probe("X-1")
    finally:
        reg.load_entries = orig
    out = capsys.readouterr().out
    assert rc == 0
    assert out.strip() == "choice-only"


def test_probe_resolves_entry_ids_only():
    entries = register_mod.load_entries()
    assert register_mod.find_entry(entries, "OQ-25") is None
    rc = register_mod.cmd_probe("OQ-25")
    assert rc == 2


def test_p0_fragment_ids_exact():
    ids = {
        e["id"]
        for e in tomllib.loads((ROOT / "tests/proof/temporary.d/p0.toml").read_text())["entry"]
    }
    expected = {"TM-P0-1", "TM-P0-3", "TM-P0-6", "TM-P0-8", "TM-P0-12", "TM-P0-13"}
    gaps = [
        "A1",
        "A2",
        "A3",
        "A4",
        "B1",
        "B2",
        "B3",
        "B4",
        "B5",
        "C1",
        "C2",
        "C3",
        "C4",
        "D1",
        "D2",
        "D3",
        "D4",
        "D5",
        "E1",
        "E2",
        "E3",
    ]
    expected |= {f"TM-P0-2:G-{g}" for g in gaps}
    assert ids == expected
    assert len(ids) == 27


def test_final_accepts_absent_or_named_not_removed(monkeypatch):
    entries = [
        {
            "id": "X-1",
            "mechanism": "m",
            "introduced_by": "S0",
            "serves": [],
            "probe": "false",
            "removal_condition": "c",
            "removed_by": "L.A.2",
            "permanent": False,
        },
        {
            "id": "X-2",
            "mechanism": "m",
            "introduced_by": "S0",
            "serves": [],
            "probe": "true",
            "removal_condition": "c",
            "removed_by": "named-not-removed",
            "citation": "K-1 declined",
            "permanent": False,
        },
    ]
    monkeypatch.setattr(register_mod, "load_entries", lambda **kw: entries)
    assert register_mod.cmd_final() == 0


def test_final_rejects_present_entry(monkeypatch):
    entries = [
        {
            "id": "X-1",
            "mechanism": "m",
            "introduced_by": "S0",
            "serves": [],
            "probe": "true",
            "removal_condition": "c",
            "removed_by": "L.A.2",
            "permanent": False,
        },
    ]
    monkeypatch.setattr(register_mod, "load_entries", lambda **kw: entries)
    assert register_mod.cmd_final() == 1


def _serving_entry():
    return {
        "id": "X-1",
        "mechanism": "m",
        "introduced_by": "L.A.1",
        "serves": ["lbl-1"],
        "probe": "true",
        "removal_condition": "c",
        "removed_by": "L.A.2",
        "permanent": False,
    }


def _claim_label(monkeypatch):
    monkeypatch.setattr(
        register_mod,
        "_load_all_labels",
        lambda: [{"id": "lbl-1", "row": "R-1", "posture": "claim"}],
    )


TARGET_NODE = {"nodeid": "t::target", "gap": "G-X1", "strict_xfail": True}
GREEN_NODE = {"nodeid": "t::green", "gap": None, "strict_xfail": False}


def test_claim_label_registered_only_by_strict_xfail_targets_is_not_a_claim(monkeypatch):
    """p0-court L.P0-1A.*: a target label at posture claim is served by its
    TM-P0-2 entry until the flip leaf, and J0-9 still needs exit 0."""
    _claim_label(monkeypatch)
    registrants = {"lbl-1": [TARGET_NODE, dict(TARGET_NODE, nodeid="t::target2")]}
    assert register_mod.register_violations([_serving_entry()], registrants) == []


def test_claim_label_with_a_green_registrant_fails_while_served(monkeypatch):
    _claim_label(monkeypatch)
    assert register_mod.register_violations([_serving_entry()], {"lbl-1": [GREEN_NODE]}) == [
        "lbl-1"
    ]


def test_claim_label_with_red_target_beside_a_preserved_node_is_not_a_claim(monkeypatch):
    """MC-02: one red strict-xfail registrant keeps the label UNPROVEN
    (e.g. WR-PROOF-2:pack-docker-live: alpine-skipped compat node + G-E2
    target)."""
    _claim_label(monkeypatch)
    assert (
        register_mod.register_violations([_serving_entry()], {"lbl-1": [TARGET_NODE, GREEN_NODE]})
        == []
    )


def test_claim_label_with_no_registrant_fails_while_served(monkeypatch):
    _claim_label(monkeypatch)
    assert register_mod.register_violations([_serving_entry()], {}) == ["lbl-1"]
