"""G3 (docs/decisions/plain-plugin-stop-grace.md): `@trestle(stop_grace=...)` gives a plain plugin
a window between a stop and the kill. A plugin that checks `ctx.cancelled` in a loop writes its
final status artifact before it is stopped, after a cancel and at its release point alike; one
that declares no grace is killed first, as before."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from trestle.common.types import DeclaredMetadata, RunView
from trestle.server.main import create_kernel
from trestle.server.plugin_schema import DeclarationError, declared_from_source

PLUGIN = """
import time

from trestle.plugin import Context, trestle


@trestle{decorator}
def {name}(ctx: Context) -> dict[str, bool]:
    while not ctx.cancelled:
        time.sleep(0.05)
    time.sleep(1.0)  # clean-up that outlasts the supervisor's poll
    status = ctx.tmp / "final.md"
    status.write_text("stopped cleanly\\n", encoding="utf-8")
    ctx.attach(status, name="final-status.md")
    return {{"stopped": True}}
"""


def _kernel(tmp_path: Path, plugins: dict[str, str]) -> Any:
    home = tmp_path / "home"
    home.mkdir()
    folder = tmp_path / "plugins"
    folder.mkdir()
    for name, decorator in plugins.items():
        (folder / f"{name}.py").write_text(
            PLUGIN.format(name=name, decorator=decorator), encoding="utf-8"
        )
    return create_kernel(home=home, plugin_dirs=[folder], skip_recovery=True)


def _artifact_names(kernel: Any, run_id: str) -> set[str]:
    listing = kernel.control.query("run_artifacts", {"run_id": run_id})
    return {row["name"] for row in listing["items"]}


def _finish(kernel: Any, run_id: str) -> RunView:
    kernel.control.await_runs([run_id], timeout_ms=30_000)
    view = kernel.control.project.status(run_id)
    assert isinstance(view, RunView)
    return view


def test_a_cancelled_plugin_with_a_grace_writes_its_final_status(tmp_path: Path) -> None:
    kernel = _kernel(tmp_path, {"graceful": "(stop_grace=5)", "abrupt": ""})
    runs = {}
    for name in ("graceful", "abrupt"):
        view = kernel.control.run(plugin=name, args={}, wait_ms=300)
        assert isinstance(view, RunView) and view.state == "running", view
        runs[name] = view.run_id
    time.sleep(0.3)
    for run_id in runs.values():
        kernel.control.cancel(run_id)
    graceful = _finish(kernel, runs["graceful"])
    abrupt = _finish(kernel, runs["abrupt"])
    assert graceful.state == "cancelled" and abrupt.state == "cancelled"
    assert "final-status.md" in _artifact_names(kernel, runs["graceful"])
    assert "final-status.md" not in _artifact_names(kernel, runs["abrupt"])


def test_at_the_release_point_the_plugin_sees_the_stop_and_finishes(tmp_path: Path) -> None:
    kernel = _kernel(tmp_path, {"timed": "(deadline=4, stop_grace=3)"})
    started = time.monotonic()
    view = kernel.control.run(plugin="timed", args={}, wait_ms=300)
    assert isinstance(view, RunView)
    done = _finish(kernel, view.run_id)
    assert done.state == "timed_out"
    assert time.monotonic() - started < 4 + 10  # the deadline holds: the window is inside it
    assert "final-status.md" in _artifact_names(kernel, view.run_id)


def test_stop_grace_is_a_literal_recorded_only_when_declared() -> None:
    def declared(decorator: str) -> DeclaredMetadata:
        return declared_from_source(PLUGIN.format(name="p", decorator=decorator))

    assert declared("(stop_grace=2.5)").stop_grace_s == 2.5
    assert "stop_grace_s" in declared("(stop_grace=2)").declared_dict()
    assert "stop_grace_s" not in declared("").declared_dict()  # identity unchanged otherwise
    assert declared("(stop_grace=None)").stop_grace_s is None
    for bad in ("(stop_grace=0)", "(stop_grace=-1)", "(stop_grace='5')", "(stop_grace=True)"):
        with pytest.raises(DeclarationError):
            declared(bad)
    manifest = {"declared": declared("(stop_grace=4)").declared_dict()}
    assert DeclaredMetadata.from_manifest(manifest).stop_grace_s == 4
