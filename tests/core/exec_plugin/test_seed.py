"""Change 2c: `trestle init` seeds `exec.py` (the guide example, byte for byte), only when it is
missing, and `--no-seed` writes neither seed."""

from __future__ import annotations

from pathlib import Path

import pytest

from trestle import cli
from trestle.server import authoring
from trestle.server.init_cmd import EXEC_ROOTS_NOTE


def _init(capsys: pytest.CaptureFixture[str], home: Path, *extra: str) -> list[str]:
    assert cli.main(["init", "--home", str(home), *extra]) == 0
    return capsys.readouterr().out.splitlines()


def test_init_seeds_exec_from_the_guide(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    home = tmp_path / "home"
    lines = _init(capsys, home)
    seeded = home / "plugins" / "exec.py"
    assert seeded.read_text(encoding="utf-8") == authoring.plugin_example("exec")
    assert (home / "plugins" / "echo.py").exists()
    assert "seeded: echo.py, exec.py" in lines
    assert EXEC_ROOTS_NOTE in lines
    config = home / "config.toml"  # init writes no roots: they are the operator's choice
    assert not config.exists() or "TRESTLE_EXEC_ROOTS" not in config.read_text(encoding="utf-8")


def test_init_leaves_an_existing_exec_alone(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    home = tmp_path / "home"
    (home / "plugins").mkdir(parents=True)
    mine = home / "plugins" / "exec.py"
    mine.write_text("# the operator's own\n", encoding="utf-8")
    _init(capsys, home)
    assert mine.read_text(encoding="utf-8") == "# the operator's own\n"
    mine.unlink()
    _init(capsys, home)  # a deleted seed comes back: the off switch is the roots, not the file
    assert mine.read_text(encoding="utf-8") == authoring.plugin_example("exec")


def test_no_seed_writes_neither(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    home = tmp_path / "home"
    lines = _init(capsys, home, "--no-seed")
    assert not (home / "plugins" / "exec.py").exists()
    assert not (home / "plugins" / "echo.py").exists()
    assert not any(line.startswith("seeded:") for line in lines)
