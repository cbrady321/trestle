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
