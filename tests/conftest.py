"""Shared pytest fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from trestle.server.main import create_kernel


@pytest.fixture
def plugin_dir() -> Path:
    return Path(__file__).resolve().parent / "fixtures" / "plugins"


@pytest.fixture
def trestle_home(tmp_path: Path, plugin_dir: Path) -> Path:
    home = tmp_path / "trestle-home"
    home.mkdir()
    return home


@pytest.fixture
def kernel(trestle_home: Path, plugin_dir: Path):
    return create_kernel(home=trestle_home, plugin_dirs=[plugin_dir], skip_recovery=True)


@pytest.fixture
def bounded_kernel(kernel, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TRESTLE_TEST_LIMITS", "1")
    kernel.registry.refresh()
    return kernel
