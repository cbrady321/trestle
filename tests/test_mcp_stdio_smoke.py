"""MCP stdio smoke — assessment session B assertions via FastMCP client."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

TOOLS_LIST_BYTE_BUDGET = 8192
REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def smoke_home() -> Path:
    home = Path(tempfile.mkdtemp(prefix="trestle-mcp-smoke-"))
    plugins = home / "plugins"
    plugins.mkdir()
    for src in (
        REPO / "examples" / "plugins",
        REPO / "tests" / "fixtures" / "plugins",
    ):
        for path in src.glob("*.py"):
            if not (plugins / path.name).exists():
                shutil.copy(path, plugins / path.name)
    return home


def test_stdio_serve_golden_path(smoke_home: Path) -> None:
    async def exercise() -> None:
        env = os.environ.copy()
        env["TRESTLE_HOME"] = str(smoke_home)

        transport = StdioTransport(
            command=sys.executable,
            args=["-m", "trestle.cli", "serve"],
            env=env,
        )

        async with Client(transport=transport) as client:
            tools = await client.list_tools()
            assert len(tools) == 9
            payload = json.dumps(
                [
                    {"name": t.name, "description": t.description, "inputSchema": t.inputSchema}
                    for t in tools
                ],
                separators=(",", ":"),
            )
            assert len(payload.encode()) <= TOOLS_LIST_BYTE_BUDGET

            catalog = (await client.call_tool("list_plugins", {})).data
            assert "echo" in {row["name"] for row in catalog["items"]}

            run = (
                await client.call_tool(
                    "run",
                    {"plugin": "echo", "args": {"message": "mcp-smoke"}, "wait_ms": 5000},
                )
            ).data
            assert run["state"] == "succeeded"
            run_id = run["run_id"]

            fetched = (
                await client.call_tool(
                    "fetch",
                    {
                        "target": f"{run_id}/result",
                        "window": {"kind": "jsonpath", "expr": "$.message"},
                    },
                )
            ).data
            assert fetched["values"] == ["mcp-smoke"]

            refusal = (await client.call_tool("run", {"plugin": "missing", "args": {}})).data
            assert refusal["origin"] == "admission"
            assert "run_id" not in refusal

    asyncio.run(exercise())


def test_stdio_run_returns_running_on_short_wait_ms(smoke_home: Path) -> None:
    """F5/F6: MCP run honors wait_ms — short deadline returns running frame."""

    async def exercise() -> None:
        env = os.environ.copy()
        env["TRESTLE_HOME"] = str(smoke_home)

        transport = StdioTransport(
            command=sys.executable,
            args=["-m", "trestle.cli", "serve"],
            env=env,
        )

        async with Client(transport=transport) as client:
            start = time.monotonic()
            run = (
                await client.call_tool(
                    "run",
                    {"plugin": "slow", "args": {"seconds": 1.0}, "wait_ms": 100},
                )
            ).data
            elapsed_ms = (time.monotonic() - start) * 1000

            assert run["state"] == "running"
            assert elapsed_ms < 500

            joined = (
                await client.call_tool(
                    "await_runs",
                    {"run_ids": [run["run_id"]], "mode": "all", "timeout_ms": 10_000},
                )
            ).data
            assert joined[0]["state"] == "succeeded"

    asyncio.run(exercise())
