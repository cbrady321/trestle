"""Tests for plugin publication config and catalog bootstrap fields."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from trestle.cli import main
from trestle.server.doctor import build_doctor_report
from trestle.server.main import create_kernel
from trestle.server.plugin_paths import CATALOG_HINT_EMPTY, resolve_plugin_dirs


def test_resolve_plugin_dirs_defaults_to_home_plugins(tmp_path: Path) -> None:
    home = tmp_path / "trestle"
    assert resolve_plugin_dirs(home) == [(home / "plugins").resolve()]


def test_resolve_plugin_dirs_cli_overrides_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "trestle"
    home.mkdir()
    cli_dir = tmp_path / "cli-plugins"
    cli_dir.mkdir()
    config_dir = tmp_path / "config-plugins"
    (home / "config.toml").write_text(
        f'[plugins]\npaths = ["{config_dir}"]\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("TRESTLE_PLUGIN_DIRS", str(tmp_path / "env-plugins"))
    assert resolve_plugin_dirs(home, cli_dirs=[cli_dir]) == [cli_dir.resolve()]


def test_resolve_plugin_dirs_config_before_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "trestle"
    home.mkdir()
    config_dir = tmp_path / "config-plugins"
    config_dir.mkdir()
    (home / "config.toml").write_text(
        f'[plugins]\npaths = ["{config_dir}"]\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("TRESTLE_PLUGIN_DIRS", str(tmp_path / "env-plugins"))
    assert resolve_plugin_dirs(home) == [config_dir.resolve()]


def test_resolve_plugin_dirs_env_when_no_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "trestle"
    home.mkdir()
    env_dir = tmp_path / "env-plugins"
    env_dir.mkdir()
    monkeypatch.setenv("TRESTLE_PLUGIN_DIRS", str(env_dir))
    assert resolve_plugin_dirs(home) == [env_dir.resolve()]


def test_list_plugins_includes_search_paths_and_hint_when_empty(tmp_path: Path) -> None:
    home = tmp_path / "trestle"
    plugin_dir = home / "plugins"
    plugin_dir.mkdir(parents=True)
    kernel = create_kernel(home=home, plugin_dirs=[plugin_dir], skip_recovery=True)
    catalog = kernel.registry.catalog().to_dict()
    assert catalog["items"] == []
    assert catalog["plugin_search_paths"] == [str(plugin_dir.resolve())]
    assert catalog["catalog_hint"] == CATALOG_HINT_EMPTY


def test_list_plugins_omits_hint_when_populated(
    tmp_path: Path,
    plugin_dir: Path,
    trestle_home: Path,
) -> None:
    kernel = create_kernel(home=trestle_home, plugin_dirs=[plugin_dir], skip_recovery=True)
    catalog = kernel.registry.catalog().to_dict()
    assert catalog["items"]
    assert "catalog_hint" not in catalog


def test_duplicate_plugin_name_first_path_wins(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    plugin_src = Path(__file__).resolve().parent / "fixtures" / "plugins" / "echo.py"
    (first / "echo.py").write_text(plugin_src.read_text(encoding="utf-8"), encoding="utf-8")
    (second / "echo.py").write_text(plugin_src.read_text(encoding="utf-8"), encoding="utf-8")
    home = tmp_path / "home"
    kernel = create_kernel(home=home, plugin_dirs=[first, second], skip_recovery=True)
    assert "echo" in kernel.registry.snapshots
    assert len(kernel.registry.snapshots) == 1
    log = (home / "service.log").read_text(encoding="utf-8")
    assert "duplicate plugin name 'echo'" in log


def test_doctor_lists_plugin_search_paths(tmp_path: Path) -> None:
    home = tmp_path / "trestle"
    plugin_dir = home / "plugins"
    plugin_dir.mkdir(parents=True)
    plugin_src = Path(__file__).resolve().parent / "fixtures" / "plugins" / "echo.py"
    (plugin_dir / "echo.py").write_text(plugin_src.read_text(encoding="utf-8"), encoding="utf-8")
    report = build_doctor_report(home=home, plugin_dirs=[plugin_dir])
    lines = report.lines()
    assert any(line.startswith("plugin_search_paths:") for line in lines)
    assert any("(1 plugins)" in line for line in lines)


def test_trestle_init_creates_home_and_seeds_echo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "trestle"
    monkeypatch.setenv("TRESTLE_HOME", str(home))
    assert main(["init"]) == 0
    assert (home / "plugins" / "echo.py").exists()
    assert (home / "service_epoch").exists()
