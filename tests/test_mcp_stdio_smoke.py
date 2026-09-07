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
from typing import Any

import pytest
from fastmcp import Client
from fastmcp.client.transports import StdioTransport

from trestle.common import codes
from trestle.query.catalog import VIEW_CATALOG_URI
from trestle.query.views import FETCH_WINDOW_KINDS, VIEW_NAMES

TOOLS_LIST_BYTE_BUDGET = 8192
REPO = Path(__file__).resolve().parents[1]


def _assert_query_fetch_dialect(tools: list[Any]) -> None:
    assert len(tools) == 10
    query = next(tool for tool in tools if tool.name == "query")
    assert set(query.inputSchema["properties"]["view"]["enum"]) == VIEW_NAMES
    fetch = next(tool for tool in tools if tool.name == "fetch")
    window = fetch.inputSchema["properties"]["window"]
    if "$ref" in window:
        ref = window["$ref"].rsplit("/", 1)[-1]
        window = fetch.inputSchema["$defs"][ref]
    assert window["properties"]["kind"]["enum"] == list(FETCH_WINDOW_KINDS)


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
            _assert_query_fetch_dialect(tools)
            payload = json.dumps(
                [
                    {"name": t.name, "description": t.description, "inputSchema": t.inputSchema}
                    for t in tools
                ],
                separators=(",", ":"),
            )
            assert len(payload.encode()) <= TOOLS_LIST_BYTE_BUDGET

            resources = await client.list_resources()
            uris = {str(resource.uri) for resource in resources}
            assert VIEW_CATALOG_URI in uris
            catalog_raw = await client.read_resource(VIEW_CATALOG_URI)
            catalog_text = catalog_raw[0].text
            catalog = json.loads(catalog_text)
            assert {row["name"] for row in catalog["views"]} == VIEW_NAMES
            assert "last_error" in catalog_text
            assert "run_events" in catalog_text
            assert "run_tail" in catalog_text
            assert "{run_id}/result" in catalog_text
            assert "art_" in catalog_text
            assert "input_schema" not in catalog_text
            assert "return_schema" not in catalog_text

            plugins = (await client.call_tool("list_plugins", {})).data
            assert "echo" in {row["name"] for row in plugins["items"]}
            assert "input_schema" not in json.dumps(plugins)
            assert "return_schema" not in json.dumps(plugins)

            described = (await client.call_tool("describe_plugin", {"plugin_id": "echo"})).data
            assert "message" in described["input_schema"]["properties"]
            assert described["return_schema"]["type"] == "object"

            bad_args = (
                await client.call_tool(
                    "run",
                    {"plugin": "echo", "args": {"message": 1}, "wait_ms": 0},
                )
            ).data
            assert bad_args["origin"] == "admission"
            assert bad_args["code"] == codes.INVALID_ARGS
            assert "run_id" not in bad_args

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

            unknown = (await client.call_tool("query", {"view": "plugin_stats", "params": {}})).data
            assert unknown["code"] == codes.INVALID_VIEW
            assert unknown["origin"] == "projection"

            sql = (
                await client.call_tool(
                    "query",
                    {"view": "SELECT 1 FROM runs", "params": {}},
                )
            ).data
            assert sql["code"] == codes.INVALID_VIEW
            assert sql["origin"] == "projection"

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


def test_stdio_empty_catalog_includes_bootstrap_fields() -> None:
    """APL-01: empty catalog exposes plugin_search_paths and catalog_hint on the wire."""

    async def exercise() -> None:
        home = Path(tempfile.mkdtemp(prefix="trestle-mcp-empty-"))
        (home / "plugins").mkdir()
        env = os.environ.copy()
        env["TRESTLE_HOME"] = str(home)

        transport = StdioTransport(
            command=sys.executable,
            args=["-m", "trestle.cli", "serve"],
            env=env,
        )

        async with Client(transport=transport) as client:
            tools = await client.list_tools()
            assert len(tools) == 10

            catalog = (await client.call_tool("list_plugins", {})).data
            assert catalog["items"] == []
            assert catalog["plugin_search_paths"] == [str((home / "plugins").resolve())]
            assert "catalog_hint" in catalog
            assert "publish_plugin" in catalog["catalog_hint"]

    asyncio.run(exercise())
