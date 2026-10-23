"""Change 2c: the `exec` plugin (the guide example `trestle init` seeds) in-process, through
`trestle.plugin.testing.call_plugin` and the real `FakeContext.exec`."""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from trestle.plugin import Blocked
from trestle.plugin.testing import FakeContext, call_plugin
from trestle.server.init_cmd import seed_exec_source

EXAMPLE = (
    Path(__file__).resolve().parents[3] / "trestle" / "plugin" / "_guide" / "examples" / "exec.py"
)


@pytest.fixture(scope="module")
def mod() -> ModuleType:
    spec = importlib.util.spec_from_file_location("trestle_exec_example", EXAMPLE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    monkeypatch.setenv("TRESTLE_EXEC_ROOTS", f"{tmp_path / 'other'}{os.pathsep}{allowed}")
    return allowed


def _call(mod: ModuleType, tmp_path: Path, args: dict[str, Any], **ctx: Any) -> dict[str, Any]:
    out = call_plugin(mod.exec, args, ctx=FakeContext(tmp_path / "ctx", **ctx))
    assert isinstance(out, dict)
    return out


def _code(out: dict[str, Any]) -> str:
    assert out["ok"] is False and isinstance(out["error"], str), out
    return str(out["error"]).split(":", 1)[0]


def test_the_seed_is_the_example() -> None:
    assert seed_exec_source() == EXAMPLE.read_bytes()


def test_ok_path(mod: ModuleType, tmp_path: Path, root: Path) -> None:
    argv = [sys.executable, "-c", "import os; print(os.getcwd())"]
    out = _call(mod, tmp_path, {"argv": argv, "cwd": str(root)})
    assert out["ok"] is True and out["exit_code"] == 0 and out["error"] is None
    assert out["stdout_tail"].strip() == os.path.realpath(root)
    assert out["timed_out"] is False and out["truncated"] is False
    assert out["output"]  # the kept output's artifact handle
    assert set(out) == {
        "ok",
        "exit_code",
        "timed_out",
        "duration_s",
        "stdout_tail",
        "stderr_tail",
        "truncated",
        "output",
        "error",
    }


def test_nonzero_exit_and_tail(mod: ModuleType, tmp_path: Path, root: Path) -> None:
    script = "import sys; print('x' * 100000); print('é' * 3000, file=sys.stderr); sys.exit(3)"
    out = _call(mod, tmp_path, {"argv": [sys.executable, "-c", script], "cwd": str(root)})
    assert out["ok"] is False and out["exit_code"] == 3 and out["error"] is None
    assert out["stdout_tail"].endswith("x\n") and len(out["stdout_tail"].encode()) <= 1500
    assert len(out["stderr_tail"].encode()) <= 1500 and set(out["stderr_tail"]) <= {"é", "\n"}


def test_tail_cuts_on_utf8_and_line_boundaries(mod: ModuleType) -> None:
    assert mod.tail("short") == "short"
    lines = "".join(f"line {n:04d}\n" for n in range(1000))
    cut = mod.tail(lines)
    assert len(cut.encode()) <= 1500 and cut.startswith("line ") and cut.endswith("0999\n")
    wide = "é" * 2000
    cut = mod.tail(wide)
    assert len(cut.encode()) <= 1500 and set(cut) == {"é"}
    # a cut inside a line starts at the next line; one right after a newline keeps its line
    assert mod.tail("a" * 10 + "\n" + "b" * 1499) == "b" * 1499
    assert mod.tail("a" * 10 + "\n" + "b" * 1500) == "b" * 1500


def test_cwd_rules(mod: ModuleType, tmp_path: Path, root: Path) -> None:
    argv = [sys.executable, "-c", "pass"]
    assert _code(_call(mod, tmp_path, {"argv": argv, "cwd": "relative"})) == "cwd_not_absolute"
    missing = str(root / "missing")
    assert _code(_call(mod, tmp_path, {"argv": argv, "cwd": missing})) == "cwd_not_a_directory"
    outside = tmp_path / "outside"
    outside.mkdir()
    out = _call(mod, tmp_path, {"argv": argv, "cwd": str(outside)})
    assert _code(out) == "cwd_outside_roots"
    assert os.path.realpath(root) in out["error"]  # the detail names the roots
    # a link inside a root that points outside it is refused
    link = root / "escape"
    link.symlink_to(outside)
    assert _code(_call(mod, tmp_path, {"argv": argv, "cwd": str(link)})) == "cwd_outside_roots"
    inside = root / "sub"
    inside.mkdir()
    assert _call(mod, tmp_path, {"argv": argv, "cwd": str(inside)})["ok"] is True


@pytest.mark.parametrize("value", [None, "", "relative/dir", "/abs:relative"])
def test_roots_unset_or_relative_block(
    mod: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, value: str | None
) -> None:
    if value is None:
        monkeypatch.delenv("TRESTLE_EXEC_ROOTS", raising=False)
    else:
        monkeypatch.setenv("TRESTLE_EXEC_ROOTS", value)
    with pytest.raises(Blocked) as info:
        _call(mod, tmp_path, {"argv": ["true"], "cwd": str(tmp_path)})
    assert info.value.code == "exec_roots_unset"
    assert "TRESTLE_EXEC_ROOTS" in info.value.human_action


def test_program_not_found_and_argv_invalid(mod: ModuleType, tmp_path: Path, root: Path) -> None:
    out = _call(mod, tmp_path, {"argv": ["no-such-program-xyz"], "cwd": str(root)})
    assert _code(out) == "program_not_found" and "PATH" in out["error"]
    assert _code(_call(mod, tmp_path, {"argv": [], "cwd": str(root)})) == "argv_invalid"


def test_not_executable(mod: ModuleType, tmp_path: Path, root: Path) -> None:
    plain = root / "plain.sh"
    plain.write_text("echo hi\n")
    plain.chmod(0o644)
    out = _call(mod, tmp_path, {"argv": [str(plain)], "cwd": str(root)})
    assert _code(out) == "not_executable"


def test_no_time_left(mod: ModuleType, tmp_path: Path, root: Path) -> None:
    out = _call(mod, tmp_path, {"argv": ["true"], "cwd": str(root)}, deadline=3.0)
    assert _code(out) == "no_time_left"


def test_timeout_returns_an_answer(mod: ModuleType, tmp_path: Path, root: Path) -> None:
    argv = [sys.executable, "-c", "import time; time.sleep(60)"]
    out = _call(mod, tmp_path, {"argv": argv, "cwd": str(root)}, deadline=7.0)
    assert out["timed_out"] is True and out["exit_code"] is None and out["ok"] is False


@pytest.mark.parametrize("program", ["trestle", "/abs/path/trestle"])
def test_operator_command(mod: ModuleType, tmp_path: Path, root: Path, program: str) -> None:
    out = _call(mod, tmp_path, {"argv": [program, "doctor"], "cwd": str(root)})
    assert _code(out) == "operator_command"
    assert "operator" in out["error"]
