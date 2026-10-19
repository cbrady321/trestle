"""T1–T3 acceptance from an MCP client over streamable HTTP: a plugin that reports progress and
re-attaches `status.md` every round is observed mid-run. `await_runs` carries its latest progress
and relays progress notifications, `run_events` pages live, `run_artifacts` holds one current
`status.md`, and `fetch` returns the newest file before the run ends."""

from __future__ import annotations

import asyncio
import os
import subprocess
import tempfile
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from tests.proof import mcp_host, tolerances
from tests.single.control.test_live_runs import LIVE_STATUS_SOURCE

REPO = Path(__file__).resolve().parents[1]


def _round(lines: list[str]) -> int:
    return int(lines[-1].strip("| "))


def test_a_live_run_is_observable_from_an_mcp_client() -> None:
    home = Path(tempfile.mkdtemp(prefix="trestle-mcp-live-"))
    (home / "plugins").mkdir()
    (home / "plugins" / "live_status.py").write_text(LIVE_STATUS_SOURCE, encoding="utf-8")
    gate = home / "gate"
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO)  # the server and its plugin children run this checkout
    proc, port = mcp_host.serve_http(home, env=env)
    notes: list[str] = []

    async def on_progress(progress: float, total: float | None, message: str | None) -> None:
        notes.append(message or "")

    async def exercise() -> None:
        transport = StreamableHttpTransport(url=f"http://127.0.0.1:{port}/mcp")
        async with Client(transport=transport, timeout=tolerances.JOIN_WAIT_S * 4) as client:
            started = (
                await client.call_tool(
                    "run", {"plugin": "live_status", "args": {"gate": str(gate)}, "wait_ms": 0}
                )
            ).data
            run_id = started["run_id"]

            # T1 + T2: a bounded await returns the running frame with its latest progress, and
            # the wait relays progress lines as notifications
            frame = (
                await client.call_tool(
                    "await_runs",
                    {"run_ids": [run_id], "timeout_ms": 1500},
                    progress_handler=on_progress,
                )
            ).data
            (view,) = frame if isinstance(frame, list) else frame["result"]
            assert view["state"] == "running", view
            assert view["progress"]["message"].startswith("monitor round "), view
            assert any(f"{run_id}: monitor round " in note for note in notes), notes

            events = (
                await client.call_tool(
                    "query", {"view": "run_events", "params": {"run_id": run_id}}
                )
            ).data
            assert events["live"] is True
            assert any(row["kind"] == "progress" for row in events["items"])

            # T3: one current status.md; its handle fetches the newest table mid-run
            listed = (
                await client.call_tool(
                    "query", {"view": "run_artifacts", "params": {"run_id": run_id}}
                )
            ).data
            assert [row["name"] for row in listed["items"]] == ["status.md"]
            handle = listed["items"][0]["artifact_id"]
            first = (
                await client.call_tool("fetch", {"target": handle, "window": {"kind": "head"}})
            ).data
            await asyncio.sleep(0.3)
            listed = (
                await client.call_tool(
                    "query", {"view": "run_artifacts", "params": {"run_id": run_id}}
                )
            ).data
            newer = listed["items"][0]["artifact_id"]
            later = (
                await client.call_tool("fetch", {"target": newer, "window": {"kind": "head"}})
            ).data
            assert _round(later["lines"]) > _round(first["lines"])

            gate.touch()
            ended = (
                await client.call_tool("await_runs", {"run_ids": [run_id], "timeout_ms": 20000})
            ).data
            (final,) = ended if isinstance(ended, list) else ended["result"]
            assert final["state"] == "succeeded" and "progress" not in final
            listed = (
                await client.call_tool(
                    "query", {"view": "run_artifacts", "params": {"run_id": run_id}}
                )
            ).data
            assert "live" not in listed
            assert [row["name"] for row in listed["items"]] == ["status.md"]

    try:
        asyncio.run(exercise())
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=tolerances.PROC_WAIT_S)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
