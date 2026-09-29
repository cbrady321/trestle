"""G-B5 (BFD-18), flipped by L.CL-C1.4: the snapshot id covered plugin.py bytes only, so editing
an imported module left it unchanged. The identity now moves when a *declared* package is edited
(WR-PLAN-5). The test is written in the declared-package form (L.CL-C1.4, step 1): a plugin
using the call form `@trestle(packages=[...])`, one of whose declared package modules is
edited. An undeclared imported module is not covered by any declaration (recorded, not
snapshotted; R-J)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.proof.markers import target_check
from trestle.server.snapshots import materialize_snapshot

PLUGIN_SOURCE = '''"""G-B5 fixture: the behavior depends on a declared package (`b5_helper`)."""

from __future__ import annotations

import importlib

from trestle.plugin.surface import Context, trestle


@trestle(packages=["b5_helper"])
def imports_helper(ctx: Context) -> dict[str, str]:
    helper = importlib.import_module("b5_helper")
    return {"value": str(helper.VALUE)}
'''


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
    source.write_text(PLUGIN_SOURCE, encoding="utf-8")
    home = tmp_path / "home"

    helper.write_text("VALUE = 1\n", encoding="utf-8")
    first = materialize_snapshot(source, "imports_helper", home=home)
    helper.write_text("VALUE = 2\n", encoding="utf-8")
    second = materialize_snapshot(source, "imports_helper", home=home)
    return first.snapshot_id, second.snapshot_id


@pytest.mark.proves("WR-PLAN-5", "WR-PLAN-5:identity-covers-imports", "core", "core", "must", "CI")
def test_target_import_edit_changes_snapshot_id(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    before, after = _ids_before_and_after_helper_edit(monkeypatch, tmp_path)
    target_check(
        before != after,
        "G-B5",
        f"snapshot id stayed {before!r} after the declared package was edited",
    )
