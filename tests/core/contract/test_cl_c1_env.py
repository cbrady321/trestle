"""L.CL-C1.3: one import-environment builder; the server's working directory is never on a
plugin-code process's import path (WR-PLAN-6)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.proof import harness, tolerances
from trestle.common import pyenv
from trestle.common.types import PublishView, RequestOutcome, RunView
from trestle.server.plugin_validate import validate_plugin

SHADOW = """\
from pathlib import Path

Path(__file__).with_name("imported.marker").write_text("imported", encoding="utf-8")
VALUE = 1
"""

IMPORTS_SHADOW = """\
import cwd_shadow
from trestle.plugin.surface import Context, trestle


@trestle
def needs_shadow(ctx: Context) -> dict[str, int]:
    return {"value": cwd_shadow.VALUE}
"""

PROBE = """\
import importlib.util
import os
import sys

from trestle.plugin.surface import Context, trestle


@trestle
def probe(ctx: Context) -> dict[str, bool]:
    cwd = os.path.realpath(os.getcwd())
    return {
        "safe_path": sys.flags.safe_path,
        "shadow_importable": importlib.util.find_spec("cwd_shadow") is not None,
        "cwd_on_path": any(p == "" or os.path.realpath(p) == cwd for p in sys.path),
    }
"""


def _shadow_dir(tmp_path: Path) -> Path:
    shadow_dir = tmp_path / "server-cwd"
    shadow_dir.mkdir()
    (shadow_dir / "cwd_shadow.py").write_text(SHADOW, encoding="utf-8")
    return shadow_dir


@pytest.mark.proves(
    "WR-PLAN-6", "WR-PLAN-6:server-cwd-shadow-ignored", "core", "core", "must", "CI"
)
@pytest.mark.proves(
    "WR-PLAN-6", "WR-PLAN-6:validate-equals-execute-imports", "core", "core", "must", "CI"
)
def test_server_cwd_shadow_module_not_imported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shadow_dir = _shadow_dir(tmp_path)
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    # the server is started from a directory holding a module a plugin could name
    monkeypatch.chdir(shadow_dir)
    kernel = harness.fresh_kernel([plugin_dir], home=tmp_path / "home")

    # a plugin that needs the shadow module is refused at publication (the validator does not
    # see it either), and importing it was never attempted by any process
    refused = kernel.control.publish_plugin(IMPORTS_SHADOW)
    assert isinstance(refused, RequestOutcome), refused
    assert refused.origin == "publication"
    assert not (shadow_dir / "imported.marker").exists()

    # a plugin that only looks for it runs, and neither it nor the wrapper's child sees the
    # server's working directory on sys.path
    published = kernel.control.publish_plugin(PROBE)
    assert isinstance(published, PublishView), published
    view = kernel.control.run(plugin="probe", wait_ms=tolerances.HARNESS_WAIT_MS)
    assert isinstance(view, RunView), view
    assert view.state == "succeeded"
    assert view.summary == {"safe_path": True, "shadow_importable": False, "cwd_on_path": False}
    assert not (shadow_dir / "imported.marker").exists()


def test_validator_refuses_what_it_cannot_import_from_its_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shadow_dir = _shadow_dir(tmp_path)
    plugin = tmp_path / "plugin.py"
    plugin.write_text(IMPORTS_SHADOW, encoding="utf-8")
    monkeypatch.chdir(shadow_dir)
    error = validate_plugin(plugin)
    assert error is not None and "cwd_shadow" in error
    assert not (shadow_dir / "imported.marker").exists()


def test_validator_stdin_is_never_the_servers(tmp_path: Path) -> None:
    """A plugin that reads stdin at import gets end-of-file at once, not the server's stdin."""
    plugin = tmp_path / "plugin.py"
    plugin.write_text(
        "import sys\n\nassert sys.stdin.read() == ''\n\n"
        "from trestle.plugin.surface import Context, trestle\n\n\n"
        "@trestle\ndef reads(ctx: Context) -> dict[str, int]:\n    return {}\n",
        encoding="utf-8",
    )
    read_end, write_end = os.pipe()
    saved = os.dup(0)
    try:
        os.dup2(read_end, 0)  # fd 0 is now a pipe that never yields data or end-of-file
        assert validate_plugin(plugin) is None
    finally:
        os.dup2(saved, 0)
        for fd in (saved, read_end, write_end):
            os.close(fd)


def test_builder_passes_the_environment_through_and_adds_no_import_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PYTHONPATH", raising=False)
    monkeypatch.setenv("TRESTLE_HOME", str(tmp_path / "ambient"))
    env = pyenv.build_child_env()
    assert "PYTHONPATH" not in env
    assert env["TRESTLE_HOME"] == str(tmp_path / "ambient")
    assert pyenv.build_child_env(home=tmp_path / "h")["TRESTLE_HOME"] == str(tmp_path / "h")

    monkeypatch.setenv("PYTHONPATH", "/operator/one")
    assert pyenv.build_child_env()["PYTHONPATH"] == "/operator/one"
    assert pyenv.python_argv("-m", "x")[1:] == ["-P", "-m", "x"]
    # the builder returns a copy: editing it never edits the server's own environment
    pyenv.build_child_env()["PYTHONPATH"] = "changed"
    assert os.environ["PYTHONPATH"] == "/operator/one"
