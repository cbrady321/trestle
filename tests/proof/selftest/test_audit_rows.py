"""Selftest for `meta audit-rows` (L.P0-0c.2): CSC-1 label schema validation
and the row-coverage report."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from tests.proof import meta

ROOT = Path(__file__).resolve().parents[3]


def _write_labels(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, labels_toml: str) -> Path:
    labels_dir = tmp_path / "labels.d"
    labels_dir.mkdir()
    (labels_dir / "fragment.toml").write_text(labels_toml)
    monkeypatch.setattr(meta, "ROOT", tmp_path)
    return labels_dir


def test_planted_unowned_row_reported(capsys: pytest.CaptureFixture[str]) -> None:
    from argparse import Namespace

    rc = meta.cmd_audit_rows(Namespace())
    assert rc == 0
    out = capsys.readouterr().out
    # WR-VERIFY-5 is one of the nine matrix-credited rows (B4.5), so it must
    # never show up as unowned even though it likely carries no direct
    # <row>:<label> entry in labels.d/p0.toml.
    assert "unowned: WR-VERIFY-5" not in out


def test_label_from_any_fragment_counted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A label declared in a non-p0.toml fragment still counts (L.P0-0c.2's
    labels.d loader reads every *.toml fragment, not just the phase's own)."""
    labels_dir = tmp_path / "labels.d"
    labels_dir.mkdir(parents=True)
    (labels_dir / "other-phase.toml").write_text(
        """
[[label]]
id = "WR-EVID-11:preserved"
row = "WR-EVID-11"
step = "core"
slice = "core"
tier = "must"
venue = "CI"
posture = "claim"
declared_by = "test"
"""
    )
    monkeypatch.setattr(
        meta,
        "_load_all_labels",
        lambda: tomllib.loads((labels_dir / "other-phase.toml").read_text())["label"],
    )
    from argparse import Namespace

    monkeypatch.chdir(ROOT)
    rc = meta.cmd_audit_rows(Namespace())
    assert rc == 0


def test_label_schema_exact(monkeypatch: pytest.MonkeyPatch) -> None:
    bad = [{"id": "X:y", "row": "X", "step": "core", "unexpected_key": 1}]
    monkeypatch.setattr(meta, "_load_all_labels", lambda: bad)
    from argparse import Namespace

    rc = meta.cmd_audit_rows(Namespace())
    assert rc == 1


def test_gated_label_without_oq_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    bad = [
        {
            "id": "X:y",
            "row": "X",
            "step": "core",
            "slice": "core",
            "tier": "must",
            "venue": "CI",
            "posture": "gated_on",
            "declared_by": "test",
        }
    ]
    monkeypatch.setattr(meta, "_load_all_labels", lambda: bad)
    from argparse import Namespace

    rc = meta.cmd_audit_rows(Namespace())
    assert rc == 1


def test_composes_must_name_mc03_clause(monkeypatch: pytest.MonkeyPatch) -> None:
    bad = [
        {
            "id": "X:y",
            "row": "X",
            "step": "core",
            "slice": "core",
            "tier": "must",
            "venue": "CI",
            "posture": "claim",
            "declared_by": "test",
            "composes": "NOT-A-CLAUSE",
        }
    ]
    monkeypatch.setattr(meta, "_load_all_labels", lambda: bad)
    from argparse import Namespace

    rc = meta.cmd_audit_rows(Namespace())
    assert rc == 1

    good = [dict(bad[0], composes="A1.1")]
    monkeypatch.setattr(meta, "_load_all_labels", lambda: good)
    rc = meta.cmd_audit_rows(Namespace())
    assert rc == 0


def test_posture_outside_c9_vocabulary_rejected(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    """CSC-1 loader: `proven`/`unproven` are not C.9 postures."""
    from argparse import Namespace

    def label(posture: str) -> dict[str, str]:
        return {
            "id": "X:y",
            "row": "X",
            "step": "core",
            "slice": "core",
            "tier": "must",
            "venue": "CI",
            "posture": posture,
            "declared_by": "test",
        }

    for bad in ("proven", "unproven", ""):
        monkeypatch.setattr(meta, "_load_all_labels", lambda bad=bad: [label(bad)])
        assert meta.cmd_audit_rows(Namespace()) == 1
        assert "is not one of" in capsys.readouterr().out
    for good in meta.CSC1_POSTURES:
        extra = {"oq": "OQ-1"} if good == "gated_on" else {}
        monkeypatch.setattr(meta, "_load_all_labels", lambda g=good, e=extra: [{**label(g), **e}])
        assert meta.cmd_audit_rows(Namespace()) == 0


# --- L.CZ.2: `audit-rows --enforce` ---------------------------------------------------------

PROVEN = {"status": "PROVEN", "corroborating_314": False, "n_results": 1}
UNPROVEN = {"status": "UNPROVEN", "corroborating_314": False, "n_results": 1}


def _label(row: str, suffix: str = "x", posture: str = "claim", **extra) -> dict:
    return {
        "id": f"{row}:{suffix}",
        "row": row,
        "step": "core",
        "slice": "core",
        "tier": "LOGIC",
        "venue": "CI",
        "posture": posture,
        "declared_by": "test",
        **extra,
    }


def _row_world(labels: list[dict], report: dict, clauses: list[dict] | None = None):
    return meta.EnforceWorld(
        scope="ci",
        report=report,
        labels=labels,
        clauses=clauses or [],
        markers={"A1.2": [("LOGIC", "CI")]},
    )


@pytest.mark.proves(
    "WR-PROOF-2", "WR-PROOF-2:no-unrun-verifier-counts", "core", "CZ", "LOGIC", "CI"
)
def test_planted_orphan_row_fails_enforce(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from argparse import Namespace

    rows = [{"id": "WR-A-1"}, {"id": "WR-A-2"}, {"id": "WR-A-3"}, {"id": "WR-A-4"}]
    clauses = [{"id": "A1.2", "rows": ["WR-A-4"], "parts": []}]
    labels = [
        _label("WR-A-1"),
        _label("WR-A-2", posture="gated_on", oq="OQ-25"),
        _label("WR-A-3", posture="na", reason="no such surface"),
    ]
    world = _row_world(labels, {"WR-A-1:x": PROVEN, "A1.2": PROVEN}, clauses)
    assert meta.audit_rows_problems(rows, labels, clauses, world) == []

    # a planted row with no registered clause fails
    orphan = rows + [{"id": "WR-ORPHAN-1"}]
    found = meta.audit_rows_problems(orphan, labels, clauses, world)
    assert found == ["WR-ORPHAN-1: no registered clause"]

    # a label whose test never ran, or ran unproven, does not own its row (WR-PROOF-2), and a
    # matrix-credited row is owned only while its clause is proven
    unrun = _row_world(labels, {"A1.2": PROVEN}, clauses)
    assert [p.split(":")[0] for p in meta.audit_rows_problems(rows, labels, clauses, unrun)] == [
        "WR-A-1"
    ]
    red = _row_world(labels, {"WR-A-1:x": PROVEN, "A1.2": UNPROVEN}, clauses)
    assert [p.split(":")[0] for p in meta.audit_rows_problems(rows, labels, clauses, red)] == [
        "WR-A-4"
    ]

    # the command: exit 1 with the orphan named, exit 0 without it
    owners = tmp_path / "row_owners.toml"
    owners.write_text("".join(f'[[row]]\nid = "{r["id"]}"\n' for r in orphan))
    monkeypatch.setattr(meta, "ROW_OWNERS_PATH", owners)
    monkeypatch.setattr(meta, "_load_all_labels", lambda: labels)
    monkeypatch.setattr(meta, "live_world", lambda scope, commit="HEAD": world)
    assert meta.cmd_audit_rows(Namespace(enforce=True)) == 1
    assert "WR-ORPHAN-1: no registered clause" in capsys.readouterr().out
    owners.write_text("".join(f'[[row]]\nid = "{r["id"]}"\n' for r in rows))
    assert meta.cmd_audit_rows(Namespace(enforce=True)) == 0
    # report mode is unchanged: it never fails on an unowned row
    owners.write_text("".join(f'[[row]]\nid = "{r["id"]}"\n' for r in orphan))
    assert meta.cmd_audit_rows(Namespace()) == 0
