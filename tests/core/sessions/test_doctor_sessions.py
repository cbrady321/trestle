"""Change 2a: `trestle doctor` lists the live runs whose MCP session has ended."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.proof import mcp_host, tolerances
from trestle import cli

WAIT_MS = int(tolerances.JOIN_WAIT_S * 3 * 1000)


def _doctor(capsys: pytest.CaptureFixture[str], home: Path) -> list[str]:
    assert cli.main(["doctor", "--home", str(home)]) == 0
    return capsys.readouterr().out.splitlines()


def test_doctor_lists_a_run_whose_session_was_deleted(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with mcp_host.McpHost(home=tmp_path / "home") as host:
        peer = host.peer()
        frame = peer.call("run", {"plugin": "slow", "args": {"seconds": 60.0}, "wait_ms": 0})
        run_id = frame["run_id"]
        before = _doctor(capsys, host.home)
        assert "session_ended_runs: 0" in before
        peer.close()  # DELETE: the session ends, the run goes on
        lines = _doctor(capsys, host.home)
        at = lines.index("session_ended_runs: 1")
        row = lines[at + 1]
        assert row.startswith(f"  - {run_id} slow "), row
        assert ": session deleted at " in row and row.endswith("Z"), row
        assert host.call("cancel", {"run_id": run_id})["code"] == "projection.cancel_accepted"
        (view,) = host.call("await_runs", {"run_ids": [run_id], "timeout_ms": WAIT_MS})["result"]
        assert view["state"] == "cancelled"
        assert "session_ended_runs: 0" in _doctor(capsys, host.home)
