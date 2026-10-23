"""`trestle pack` through the CLI entry point (a wrappers-only bundled pack: no pip, no git)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trestle.cli import main

from .conftest import make_pack


def test_install_list_remove(
    tmp_path: Path,
    bundled: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("TRESTLE_BUNDLED_PACKS", str(bundled))
    make_pack(bundled, "pytest", ["pytest_run"], folder="trestle-pack-pytest")
    make_pack(bundled, "docker", ["docker_stack"], folder="trestle-pack-docker")
    home = tmp_path / "home"
    assert main(["pack", "list", "--home", str(home)]) == 0
    assert "available bundled packs: docker, pytest" in capsys.readouterr().out
    assert main(["pack", "install", "pytest", "--home", str(home)]) == 0
    assert "installed pytest@" in capsys.readouterr().out
    assert main(["pack", "list", "--json", "--home", str(home)]) == 0
    view = json.loads(capsys.readouterr().out)
    assert [row["label"].split("@")[0] for row in view["installed"]] == ["pytest"]
    assert view["available"] == ["docker"]
    assert main(["pack", "remove", "pytest", "--home", str(home)]) == 0
    assert main(["pack", "remove", "pytest", "--home", str(home)]) == 1
    assert "no installed pack 'pytest'" in capsys.readouterr().err
