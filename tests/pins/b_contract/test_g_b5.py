"""G-B5 (BFD-18): the snapshot id covers plugin.py bytes only. Pin records
that editing an imported module leaves the id unchanged; the target requires
the identity to change (WR-PLAN-5)."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from tests.proof.markers import target_check
from trestle.server.snapshots import materialize_snapshot

PLUGIN = Path(__file__).parent / "plugins" / "imports_helper.py"


def _ids_before_and_after_helper_edit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[str, str]:
    helper_dir = tmp_path / "helper"
    helper_dir.mkdir()
    helper = helper_dir / "b5_helper.py"
    monkeypatch.setenv(
        "PYTHONPATH", str(helper_dir) + os.pathsep + os.environ.get("PYTHONPATH", "")
    )
    source = tmp_path / "imports_helper.py"
    shutil.copy2(PLUGIN, source)
    home = tmp_path / "home"

    helper.write_text("VALUE = 1\n", encoding="utf-8")
    first = materialize_snapshot(source, "imports_helper", home=home)
    helper.write_text("VALUE = 2\n", encoding="utf-8")
    second = materialize_snapshot(source, "imports_helper", home=home)
    return first.snapshot_id, second.snapshot_id


@pytest.mark.pin("G-B5")
def test_pin_import_edit_keeps_snapshot_id(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    before, after = _ids_before_and_after_helper_edit(monkeypatch, tmp_path)
    assert before == after


@pytest.mark.target("G-B5")
@pytest.mark.proves("WR-PLAN-5", "WR-PLAN-5:identity-covers-imports", "core", "core", "must", "CI")
@pytest.mark.xfail(strict=True, reason="defect:G-B5")
def test_target_import_edit_changes_snapshot_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    before, after = _ids_before_and_after_helper_edit(monkeypatch, tmp_path)
    target_check(
        before != after,
        "G-B5",
        f"snapshot id stayed {before!r} after the imported module was edited",
    )
