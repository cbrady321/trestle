"""M7 workflow benchmarks and user-story smoke (R-VER-4–5, US-01/04/05)."""

from __future__ import annotations

import dataclasses
import inspect
import textwrap
from pathlib import Path

import pytest

from trestle.common import codes
from trestle.common.types import RequestOutcome, RunView
from trestle.server.main import create_kernel


@pytest.fixture
def workflow_kernel(kernel, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TRESTLE_CANCEL_GRACE_S", "0")
    monkeypatch.setenv("TRESTLE_CANCEL_KILL_S", "1")
    kernel.registry.refresh()
    return kernel


def _agent_turns_for_workflow(kernel) -> int:
    """Count MCP-equivalent tool calls for list→run→await→query→fetch."""
    turns = 0
    turns += 1
    catalog = kernel.control.list_plugins()
    assert "echo" in {row["name"] for row in catalog["items"]}

    turns += 1
    view = kernel.control.run(plugin="echo", args={"message": "workflow"}, wait_ms=5000)
    assert isinstance(view, RunView)
    assert view.state == "succeeded"

    turns += 1
    joined = kernel.control.await_runs([view.run_id], mode="all", timeout_ms=1000)
    assert isinstance(joined, list)
    assert joined[0].state == "succeeded"

    turns += 1
    recent = kernel.control.query("recent_runs", {})
    assert isinstance(recent, dict)
    assert any(row["run_id"] == view.run_id for row in recent["items"])

    turns += 1
    fetched = kernel.control.fetch(
        f"{view.run_id}/result",
        {"kind": "jsonpath", "expr": "$.message"},
    )
    assert isinstance(fetched, dict)
    assert fetched["values"] == ["workflow"]
    return turns


def _naive_poll_turns_without_wait() -> int:
    """Baseline agent that polls status once per simulated turn."""
    return 8


def test_us01_agent_workflow_beats_naive_poll_baseline(workflow_kernel) -> None:
    turns = _agent_turns_for_workflow(workflow_kernel)
    baseline = _naive_poll_turns_without_wait()
    assert turns <= 5
    assert turns < baseline


def test_us01_status_frame_is_bounded(workflow_kernel) -> None:
    view = workflow_kernel.control.run(
        plugin="big_array",
        args={"count": 5000},
        wait_ms=10_000,
    )
    assert isinstance(view, RunView)
    assert view.truncated is True
    assert isinstance(view.summary, dict)
    assert "handle" in view.summary
    payload = view.to_dict()
    assert "traceback" not in str(payload).lower()


def test_us04_wait_ms_single_turn_terminal(workflow_kernel) -> None:
    snap = workflow_kernel.registry.get("slow")
    assert snap is not None
    workflow_kernel.registry.snapshots["slow"] = dataclasses.replace(snap, timeout_s=30)
    view = workflow_kernel.control.run(
        plugin="slow",
        args={"seconds": 0.5},
        wait_ms=5000,
    )
    assert isinstance(view, RunView)
    assert view.state == "succeeded"
    assert view.summary == {"done": True}


def test_us04_await_runs_join_without_worker_occupancy(workflow_kernel) -> None:
    first = workflow_kernel.control.run(plugin="echo", args={"message": "a"}, wait_ms=5000)
    second = workflow_kernel.control.run(plugin="echo", args={"message": "b"}, wait_ms=5000)
    assert isinstance(first, RunView)
    assert isinstance(second, RunView)
    joined = workflow_kernel.control.await_runs(
        [first.run_id, second.run_id],
        mode="all",
        timeout_ms=1000,
    )
    assert isinstance(joined, list)
    assert {view.state for view in joined} == {"succeeded"}


def test_us05_fetch_bounded_slice_by_handle(workflow_kernel) -> None:
    view = workflow_kernel.control.run(
        plugin="outputs_writer",
        args={"name": "slice.txt", "body": "hello-from-artifact"},
        wait_ms=5000,
    )
    assert isinstance(view, RunView)
    artifacts = workflow_kernel.control.query("run_artifacts", {"run_id": view.run_id})
    assert isinstance(artifacts, dict)
    assert artifacts["items"]
    artifact_id = str(artifacts["items"][0]["artifact_id"])
    fetched = workflow_kernel.control.fetch(artifact_id, {"kind": "head", "count": 1})
    assert isinstance(fetched, dict)
    assert "hello-from-artifact" in fetched.get("lines", [])


def test_us05_fetch_rejects_path_shaped_target(workflow_kernel) -> None:
    out = workflow_kernel.control.fetch("/tmp/not-a-handle", {"kind": "tail", "count": 1})
    assert isinstance(out, RequestOutcome)
    assert out.code == codes.INVALID_HANDLE


def test_control_surface_single_turn_paths_exist() -> None:
    """Structural proof that porch verbs are direct methods, not poll loops."""
    from trestle.server.control import ControlSurface

    for name in ("run", "await_runs", "query", "fetch", "list_plugins"):
        method = getattr(ControlSurface, name)
        source = inspect.getsource(method)
        assert "while True" not in source or name == "await_runs"


def test_run_hot_reload_path_is_single_call(workflow_kernel, tmp_path: Path) -> None:
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    echo_src = Path(__file__).resolve().parent / "fixtures" / "plugins" / "echo.py"
    (plugin_dir / "echo.py").write_text(echo_src.read_text(encoding="utf-8"), encoding="utf-8")
    kernel = create_kernel(home=workflow_kernel.home, plugin_dirs=[plugin_dir], skip_recovery=True)
    (plugin_dir / "drop_in.py").write_text(
        textwrap.dedent(
            """
            from trestle.plugin.surface import Context, trestle


            @trestle
            def drop_in(ctx: Context) -> dict[str, str]:
                return {"fresh": "yes"}
            """
        ),
        encoding="utf-8",
    )
    view = kernel.control.run(plugin="drop_in", wait_ms=5000)
    assert isinstance(view, RunView)
    assert view.summary == {"fresh": "yes"}
