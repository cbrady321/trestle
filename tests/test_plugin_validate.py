"""Tests for pack plugin import validation."""

from __future__ import annotations

from pathlib import Path

from trestle.server.plugin_validate import (
    plugin_imports_packs,
    validate_plugin_imports,
)


def test_plugin_imports_packs_detects_from_import(tmp_path: Path) -> None:
    path = tmp_path / "plugin.py"
    path.write_text(
        "from trestle_packs.docker.runner import StackRunner\n",
        encoding="utf-8",
    )
    assert plugin_imports_packs(path) is True


def test_plugin_imports_packs_ignores_echo(tmp_path: Path) -> None:
    path = tmp_path / "plugin.py"
    path.write_text(
        "from trestle.plugin.surface import Context, trestle\n",
        encoding="utf-8",
    )
    assert plugin_imports_packs(path) is False


def test_validate_plugin_imports_none_for_echo(tmp_path: Path) -> None:
    path = tmp_path / "plugin.py"
    path.write_text("import json\n", encoding="utf-8")
    assert validate_plugin_imports(path) is None
