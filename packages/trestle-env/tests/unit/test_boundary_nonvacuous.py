"""The root import-boundary check (C.5 step 4) is not vacuous over `trestle_env` (L.RB-0.1)."""

from __future__ import annotations

from pathlib import Path

from tests.proof import test_import_boundaries as boundaries


def test_root_boundary_check_scans_trestle_env() -> None:
    modules = boundaries.env_modules()
    assert modules, "the root boundary check scans no trestle_env module"
    assert any(m.name == "__init__.py" and m.parent == boundaries.ENV_DIR for m in modules)
    assert boundaries.scan_env_violations() == []

    # A planted in-memory `trestle.server` import is flagged, in a plain module and in a
    # composition root alike; a composition root may bind `trestle_packs`, a plain module may not.
    planted = "import os\nfrom trestle.server import runs\n"
    for root in (False, True):
        found = boundaries.scan_env_source(planted, "planted.py", composition_root=root)
        assert found == ["planted.py:2: trestle_env may not import 'trestle.server'"]
    packs = "from trestle_packs.container import engine\n"
    assert boundaries.scan_env_source(packs, "p.py", composition_root=True) == []
    assert boundaries.scan_env_source(packs, "p.py", composition_root=False) != []
    fine = "from __future__ import annotations\nimport json\nfrom trestle.workflow import x\n"
    assert boundaries.scan_env_source(fine, "f.py", composition_root=False) == []


def test_planted_file_under_a_scanned_directory_is_flagged(tmp_path: Path) -> None:
    (tmp_path / "plugins").mkdir()
    (tmp_path / "ok.py").write_text("import json\n")
    (tmp_path / "plugins" / "root.py").write_text("import trestle_packs\n")
    (tmp_path / "bad.py").write_text("import trestle.server\n")
    (tmp_path / "plugins" / "bad2.py").write_text("import trestle.query\n")
    found = boundaries.scan_env_violations(tmp_path)
    assert len(found) == 2
    assert any("bad.py:1" in f and "trestle.server" in f for f in found)
    assert any("bad2.py:1" in f and "trestle.query" in f for f in found)
