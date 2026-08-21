"""M4 waiting tests — wait_ms, await_runs modes, partial sequences."""

from __future__ import annotations

import ast
import dataclasses
import threading
import time
from pathlib import Path

import pytest

from trestle.common import codes
from trestle.common.types import AdmitRequest, RequestOutcome, RunView, WorkOrder


@pytest.fixture
def waiting_kernel(kernel, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TRESTLE_CANCEL_GRACE_S", "0")
    monkeypatch.setenv("TRESTLE_CANCEL_KILL_S", "1")
    kernel.registry.refresh()
    return kernel


def _patch_slow_timeout(kernel, *, timeout_s: int) -> None:
    snap = kernel.registry.get("slow")
    if snap is not None:
        kernel.registry.snapshots["slow"] = dataclasses.replace(snap, timeout_s=timeout_s)


def _start_run(kernel, plugin: str, args: dict[str, object] | None = None) -> str:
    admit = kernel.control.admission.admit(AdmitRequest(plugin=plugin, args=args or {}))
    assert admit.tag == "admitted"
    run_id = admit.run_id
    snap = kernel.registry.get(plugin)
    assert snap is not None
    from trestle.server.ledger import RunLedger, ledger_path, run_dir_for

    created = RunLedger.open(ledger_path(run_dir_for(kernel.home, run_id))).last_kind("created")
    assert created is not None
    order = WorkOrder(
        run_id=run_id,
        snapshot_id=snap.snapshot_id,
        spec_hash=str(created.get("spec_hash", "")),
    )
    thread = threading.Thread(
        target=kernel.control.conductor.drive,
        args=(order,),
        daemon=True,
    )
    thread.start()
    return run_id


def test_run_wait_ms_returns_default_agent_success_on_terminal(waiting_kernel) -> None:
    view = waiting_kernel.control.run(
        plugin="echo",
        args={"message": "waited"},
        wait_ms=5000,
    )
    assert isinstance(view, RunView)
    assert view.state == "succeeded"
    assert view.summary == {"message": "waited"}
    assert view.truncated is False
    assert view.duration_ms is not None


def test_run_wait_ms_slow_plugin_long_stdio_wait(waiting_kernel) -> None:
    start = time.monotonic()
    view = waiting_kernel.control.run(
        plugin="slow",
        args={"seconds": 1.0},
        wait_ms=5000,
    )
    elapsed_ms = (time.monotonic() - start) * 1000
    assert isinstance(view, RunView)
    assert view.state == "succeeded"
    assert view.summary == {"done": True}
    assert elapsed_ms >= 900
    assert elapsed_ms < 5000


def test_run_wait_ms_timeout_returns_running_frame(waiting_kernel) -> None:
    slow_id = _start_run(waiting_kernel, "slow", {"seconds": 30.0})
    time.sleep(0.2)
    view = waiting_kernel.control.project.await_one(slow_id, wait_ms=200)
    assert isinstance(view, RunView)
    assert view.state == "running"
    assert view.summary is None
    assert view.next is None


def test_await_runs_all_waits_for_every_terminal(waiting_kernel) -> None:
    slow_id = _start_run(waiting_kernel, "slow", {"seconds": 0.8})
    fast_id = _start_run(waiting_kernel, "echo", {"message": "fast"})
    result = waiting_kernel.control.await_runs(
        [slow_id, fast_id],
        mode="all",
        timeout_ms=10_000,
    )
    assert isinstance(result, list)
    assert [view.run_id for view in result] == [slow_id, fast_id]
    assert result[0].state == "succeeded"
    assert result[1].state == "succeeded"


def test_await_runs_any_returns_when_one_terminal(waiting_kernel) -> None:
    slow_id = _start_run(waiting_kernel, "slow", {"seconds": 30.0})
    fast_id = _start_run(waiting_kernel, "echo", {"message": "fast"})
    start = time.monotonic()
    result = waiting_kernel.control.await_runs(
        [slow_id, fast_id],
        mode="any",
        timeout_ms=10_000,
    )
    elapsed_ms = (time.monotonic() - start) * 1000
    assert isinstance(result, list)
    assert [view.run_id for view in result] == [slow_id, fast_id]
    assert result[1].state == "succeeded"
    assert result[0].state in {"queued", "running"}
    assert elapsed_ms < 5000


def test_await_runs_first_failure_returns_on_failure_terminal(waiting_kernel) -> None:
    slow_ok = _start_run(waiting_kernel, "slow", {"seconds": 60.0})
    _patch_slow_timeout(waiting_kernel, timeout_s=2)
    fast_fail = _start_run(waiting_kernel, "slow", {"seconds": 30.0})
    start = time.monotonic()
    result = waiting_kernel.control.await_runs(
        [slow_ok, fast_fail],
        mode="first_failure",
        timeout_ms=30_000,
    )
    elapsed_ms = (time.monotonic() - start) * 1000
    assert isinstance(result, list)
    assert [view.run_id for view in result] == [slow_ok, fast_fail]
    assert result[0].state in {"queued", "running"}
    assert result[1].state == "timed_out"
    assert elapsed_ms < 10_000


def test_await_runs_first_failure_all_succeeded(waiting_kernel) -> None:
    first = _start_run(waiting_kernel, "echo", {"message": "one"})
    second = _start_run(waiting_kernel, "echo", {"message": "two"})
    result = waiting_kernel.control.await_runs(
        [first, second],
        mode="first_failure",
        timeout_ms=10_000,
    )
    assert isinstance(result, list)
    assert all(view.state == "succeeded" for view in result)


def test_await_runs_timeout_partial_sequence_preserves_input_order(waiting_kernel) -> None:
    first = _start_run(waiting_kernel, "slow", {"seconds": 30.0})
    second = _start_run(waiting_kernel, "slow", {"seconds": 30.0})
    result = waiting_kernel.control.await_runs(
        [first, second],
        mode="all",
        timeout_ms=300,
    )
    assert isinstance(result, list)
    assert [view.run_id for view in result] == [first, second]
    assert all(view.state in {"queued", "running"} for view in result)
    assert all(view.summary is None for view in result)


def test_await_runs_invalid_handle_fails_entire_batch(waiting_kernel) -> None:
    run_id = _start_run(waiting_kernel, "echo", {"message": "ok"})
    result = waiting_kernel.control.await_runs(
        [run_id, "r_missing"],
        mode="all",
        timeout_ms=5000,
    )
    assert isinstance(result, RequestOutcome)
    assert result.code == codes.INVALID_HANDLE
    assert result.origin == "projection"


def test_await_runs_invalid_mode_refusal(waiting_kernel) -> None:
    run_id = _start_run(waiting_kernel, "echo", {"message": "ok"})
    result = waiting_kernel.control.await_runs(
        [run_id],
        mode="first",
        timeout_ms=5000,
    )
    assert isinstance(result, RequestOutcome)
    assert result.code == codes.PROJECTION_INVALID_ARGS


def test_no_tasks_mapper_in_server(waiting_kernel) -> None:
    repo = Path(__file__).resolve().parents[1]
    tree = ast.parse((repo / "trestle/server/main.py").read_text(encoding="utf-8"))
    forbidden = {"docket", "redis", "fastmcp.server.tasks", "fastmcp_tasks"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name not in forbidden
                assert not any(alias.name.startswith(prefix) for prefix in forbidden)
        elif isinstance(node, ast.ImportFrom) and node.module:
            assert node.module not in forbidden
            assert not any(node.module.startswith(prefix) for prefix in forbidden)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "tool":
                for keyword in node.keywords:
                    if keyword.arg == "task":
                        raise AssertionError("MCP tools must not use task=True (R-WAIT-4)")
