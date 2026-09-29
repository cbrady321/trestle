"""Self-tests pinning today's S0 behaviour for Assumption 2 (count, hold,
sever, rejoin) over `tests.proof.mcp_host` (L.P0-0b.3; MC-12).

No product code changes here — these tests observe what the kernel already
does when an MCP session is held, severed, or restarted. A `stdin_close`
or `sigkill` sever leaves the run's wrapper/child processes orphaned
today (the reaper that would attribute and clean them up is L.P0-0b.4);
`_reap_under_home` below is this test file's own cleanup so no process it
provoked survives the test, per this merge's hard rule.
"""

from __future__ import annotations

import glob
import subprocess
import time
from pathlib import Path

import pytest

from tests.proof import records, tolerances
from tests.proof.mcp_host import McpHost, rejoin


def _reap_under_home(home: Path) -> None:
    """Kill any process whose command line mentions `home` (a wrapper or
    child left behind by a severed session), best-effort."""
    marker = str(home)
    out = subprocess.run(["ps", "-eo", "pid,command"], capture_output=True, text=True).stdout
    for line in out.splitlines():
        if marker not in line:
            continue
        parts = line.strip().split(maxsplit=1)
        if not parts:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        try:
            subprocess.run(["kill", "-9", str(pid)], capture_output=True)
        except OSError:
            pass


def _run_dirs(home: Path) -> list[Path]:
    return [Path(p) for p in glob.glob(str(home / "runs" / "*" / "*"))]


def test_hold_does_not_block_other_requests_accounting() -> None:
    with McpHost() as host:
        start = time.monotonic()
        held = host.hold("run", {"plugin": "slow", "args": {"seconds": 2.0}, "wait_ms": 10_000})
        fast = host.call("list_plugins", {})
        fast_elapsed = time.monotonic() - start

        assert "slow" in {row["name"] for row in fast["items"]}
        assert fast_elapsed < 1.5, "a fast request must not wait behind a held slow one"

        result = host.join(held)
        assert result["state"] == "succeeded"
        assert host.request_count() == 3  # initialize, hold(run), call(list_plugins)


def test_sever_cancel_notification_run_reaches_terminal() -> None:
    with McpHost() as host:
        home = host.home
        held = host.hold("run", {"plugin": "slow", "args": {"seconds": 1.0}, "wait_ms": 10_000})
        time.sleep(tolerances.SETTLE_SHORT_S)
        host.sever("cancel_notification", req_id=held)
        # today the server answers a cancelled MCP request with a JSON-RPC
        # error (mcp SDK cancellation support) rather than the tool's own
        # result; the underlying run itself is not stopped by this —
        # it keeps running and reaches a terminal state on its own.
        with pytest.raises(RuntimeError, match="cancelled"):
            host.join(held, timeout=tolerances.JOIN_WAIT_S)

        deadline = time.monotonic() + 5
        run_dirs: list[Path] = []
        while time.monotonic() < deadline:
            run_dirs = _run_dirs(home)
            if run_dirs:
                break
            time.sleep(tolerances.POLL_S)
        assert len(run_dirs) == 1
        node = records.node_record(run_dirs[0])
        deadline = time.monotonic() + 5
        while node.terminal is None and time.monotonic() < deadline:
            time.sleep(tolerances.POLL_S)
            node = records.node_record(run_dirs[0])
        assert node.terminal is not None


def test_sever_close_server_exits_run_recovered_interrupted() -> None:
    """Pins a real finding: closing stdin alone does not make the server
    exit promptly while a run is in flight — `asyncio.run`'s shutdown
    gather waits out the `asyncio.to_thread` wrapping
    `Conductor.drive_async` (trestle/server/conductor.py:143-145) rather
    than cancelling it, so a graceful close only "completes" once the run
    does (plan-gap: no interruption is observable through stdin_close
    alone). A realistic client escalates to a hard kill after a short
    grace period; that is what actually interrupts the run and exercises
    recovery's "interrupted" path here."""
    host = McpHost()
    home = host.home
    try:
        host.hold("run", {"plugin": "slow", "args": {"seconds": 5.0}, "wait_ms": 200})
        time.sleep(tolerances.SETTLE_S)
        host.sever("stdin_close")
        grace_deadline = time.monotonic() + 1.0
        while host.proc.poll() is None and time.monotonic() < grace_deadline:
            time.sleep(tolerances.POLL_FINE_S)
        if host.proc.poll() is None:
            host.sever("sigkill")
        host.proc.wait(timeout=tolerances.PROC_WAIT_S)
        assert host.proc.poll() is not None, "server did not exit"
    finally:
        host.close()
        _reap_under_home(home)

    try:
        new_host = rejoin(host)  # a new session boots the same home; recovery runs on start
        try:
            run_dirs = _run_dirs(home)
            assert len(run_dirs) == 1
            node = records.node_record(run_dirs[0])
            assert node.terminal == "interrupted"
        finally:
            new_host.close()
    finally:
        _reap_under_home(home)


def test_rejoin_same_key_returns_same_run_id() -> None:
    host = McpHost()
    home = host.home
    try:
        first = host.call(
            "run",
            {
                "plugin": "echo",
                "args": {"message": "x"},
                "wait_ms": 5000,
                "idempotency_key": "rejoin-k1",
            },
        )
    finally:
        host.close()

    new_host = rejoin(host)
    try:
        assert new_host.home == home
        second = new_host.call(
            "run",
            {
                "plugin": "echo",
                "args": {"message": "x"},
                "wait_ms": 5000,
                "idempotency_key": "rejoin-k1",
            },
        )
        assert second["run_id"] == first["run_id"]
    finally:
        new_host.close()


def test_kill_server_restart_recovers_same_home() -> None:
    with McpHost() as host:
        home = host.home
        first = host.call(
            "run", {"plugin": "echo", "args": {"message": "restart"}, "wait_ms": 5000}
        )
        assert first["state"] == "succeeded"

        host.kill_server()
        assert host.proc.poll() is not None
        host.restart()
        assert host.home == home

        plugins = host.call("list_plugins", {})
        assert "echo" in {row["name"] for row in plugins["items"]}

        run_dirs = _run_dirs(home)
        assert len(run_dirs) == 1
        node = records.node_record(run_dirs[0])
        assert node.terminal == "succeeded"
