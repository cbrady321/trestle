"""MCP streamable HTTP smoke: the one agent transport, behind the agent token (E6)."""

from __future__ import annotations

import asyncio
import http.client
import json
import shutil
import tempfile
import time
from pathlib import Path

import pytest
from fastmcp import Client

from tests.proof import mcp_host
from trestle.common import codes
from trestle.query.catalog import VIEW_CATALOG_URI
from trestle.query.views import FETCH_WINDOW_KINDS, VIEW_NAMES

TOOLS_LIST_BYTE_BUDGET = 8192
REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def smoke_home() -> Path:
    home = Path(tempfile.mkdtemp(prefix="trestle-mcp-http-smoke-"))
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


def test_streamable_http_serve_golden_path(smoke_home: Path) -> None:
    async def exercise() -> None:
        # `--port 0`: the server picks a free port and says which (L-18), so two suites on one
        # host never collide on a fixed port
        proc, port = mcp_host.serve_http(smoke_home)
        try:
            async with Client(transport=mcp_host.http_transport(smoke_home, port)) as client:
                tools = await client.list_tools()
                assert len(tools) == 10
                query = next(tool for tool in tools if tool.name == "query")
                assert set(query.inputSchema["properties"]["view"]["enum"]) == VIEW_NAMES
                fetch = next(tool for tool in tools if tool.name == "fetch")
                window = fetch.inputSchema["properties"]["window"]
                if "$ref" in window:
                    ref = window["$ref"].rsplit("/", 1)[-1]
                    window = fetch.inputSchema["$defs"][ref]
                assert window["properties"]["kind"]["enum"] == list(FETCH_WINDOW_KINDS)
                payload = json.dumps(
                    [
                        {"name": t.name, "description": t.description, "inputSchema": t.inputSchema}
                        for t in tools
                    ],
                    separators=(",", ":"),
                )
                assert len(payload.encode()) <= TOOLS_LIST_BYTE_BUDGET

                resources = await client.list_resources()
                assert any(str(resource.uri) == VIEW_CATALOG_URI for resource in resources)
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
                        {"plugin": "echo", "args": {"message": "http-smoke"}, "wait_ms": 5000},
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
                assert fetched["values"] == ["http-smoke"]

                refusal = (await client.call_tool("run", {"plugin": "missing", "args": {}})).data
                assert refusal["origin"] == "admission"
                assert "run_id" not in refusal

                unknown = (
                    await client.call_tool("query", {"view": "plugin_stats", "params": {}})
                ).data
                assert unknown["code"] == codes.INVALID_VIEW
                assert unknown["origin"] == "projection"

                sql = (
                    await client.call_tool("query", {"view": "SELECT 1 FROM runs", "params": {}})
                ).data
                assert sql["code"] == codes.INVALID_VIEW
                assert sql["origin"] == "projection"
        finally:
            mcp_host.stop_http(proc)

    asyncio.run(exercise())


def test_two_servers_on_port_zero_get_two_ports(smoke_home: Path, tmp_path: Path) -> None:
    """`serve --port 0` twice on one host: each reports its own port, and both answer."""
    second_home = tmp_path / "second"
    shutil.copytree(smoke_home, second_home)
    servers = [mcp_host.serve_http(smoke_home), mcp_host.serve_http(second_home)]
    try:
        ports = [port for _, port in servers]
        assert ports[0] != ports[1], ports

        async def tools(home: Path, port: int) -> int:
            async with Client(transport=mcp_host.http_transport(home, port)) as client:
                return len(await client.list_tools())

        async def both() -> list[int]:
            homes = (smoke_home, second_home)
            return list(await asyncio.gather(*(tools(h, p) for h, p in zip(homes, ports))))

        assert asyncio.run(both()) == [10, 10]
    finally:
        for proc, _ in servers:
            mcp_host.stop_http(proc)


def test_http_run_returns_running_on_short_wait_ms(smoke_home: Path) -> None:
    """F5/F6: MCP run honors wait_ms — short deadline returns running frame."""

    async def exercise() -> None:
        proc, port = mcp_host.serve_http(smoke_home)
        try:
            async with Client(transport=mcp_host.http_transport(smoke_home, port)) as client:
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
        finally:
            mcp_host.stop_http(proc)

    asyncio.run(exercise())


def test_http_empty_catalog_includes_bootstrap_fields() -> None:
    """APL-01: empty catalog exposes plugin_search_paths and catalog_hint on the wire."""

    async def exercise() -> None:
        home = Path(tempfile.mkdtemp(prefix="trestle-mcp-empty-"))
        (home / "plugins").mkdir()
        proc, port = mcp_host.serve_http(home)
        try:
            async with Client(transport=mcp_host.http_transport(home, port)) as client:
                tools = await client.list_tools()
                assert len(tools) == 10

                catalog = (await client.call_tool("list_plugins", {})).data
                assert catalog["items"] == []
                assert catalog["plugin_search_paths"] == [str((home / "plugins").resolve())]
                assert "catalog_hint" in catalog
                assert "publish_plugin" in catalog["catalog_hint"]
        finally:
            mcp_host.stop_http(proc)

    asyncio.run(exercise())


def test_request_without_the_token_is_refused(smoke_home: Path) -> None:
    """Row 1 of the service decision: every MCP request must carry `Authorization: Bearer
    <agent.token>`; without it, or with another token, the server answers 401 and admits
    nothing. The token file is the user's alone (`0600`)."""
    proc, port = mcp_host.serve_http(smoke_home)
    try:
        token_file = smoke_home / "agent.token"
        assert token_file.stat().st_mode & 0o777 == 0o600
        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": mcp_host.PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "no-token", "version": "0"},
                },
            }
        )
        base = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        for extra in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": "Basic x"}):
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
            conn.request("POST", "/mcp", body=body, headers={**base, **extra})
            response = conn.getresponse()
            assert response.status == 401, (extra, response.status)
            response.read()
            conn.close()

        async def refused_client() -> None:
            from fastmcp.client.transports import StreamableHttpTransport

            transport = StreamableHttpTransport(url=f"http://127.0.0.1:{port}/mcp")
            async with Client(transport=transport) as client:
                await client.list_tools()

        with pytest.raises(Exception):  # noqa: B017 - any client failure; the 401 is above
            asyncio.run(refused_client())
        assert not (smoke_home / "runs").exists() or not list((smoke_home / "runs").glob("*/r_*"))
    finally:
        mcp_host.stop_http(proc)
