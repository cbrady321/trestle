"""Selftest for the import-boundary gate (L.P0-0d.5): a planted host
import is red."""

from __future__ import annotations

from tests.proof import test_import_boundaries as boundaries_mod


def test_planted_host_import_fails(tmp_path, monkeypatch):
    planted = tmp_path / "trestle" / "server"
    planted.mkdir(parents=True)
    (planted / "planted.py").write_text("import trestle_packs.core\n")

    def fake_host_modules():
        return [planted / "planted.py"]

    monkeypatch.setattr(boundaries_mod, "host_modules", fake_host_modules)
    monkeypatch.setattr(boundaries_mod, "ROOT", tmp_path)
    violations = boundaries_mod.scan_host_violations(allowlist=set())
    assert violations
    assert "trestle_packs" in violations[0]
