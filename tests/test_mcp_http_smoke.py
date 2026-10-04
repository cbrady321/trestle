"""MCP streamable HTTP smoke — E6 transport alongside stdio."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from tests.proof import mcp_host, tolerances
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
        url = f"http://127.0.0.1:{port}/mcp"
        try:
            transport = StreamableHttpTransport(url=url)
            async with Client(transport=transport) as client:
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
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=tolerances.PROC_WAIT_S)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()

    asyncio.run(exercise())


def test_two_servers_on_port_zero_get_two_ports(smoke_home: Path, tmp_path: Path) -> None:
    """`serve --port 0` twice on one host: each reports its own port, and both answer."""
    second_home = tmp_path / "second"
    shutil.copytree(smoke_home, second_home)
    servers = [mcp_host.serve_http(smoke_home), mcp_host.serve_http(second_home)]
    try:
        ports = [port for _, port in servers]
        assert ports[0] != ports[1], ports

        async def tools(port: int) -> int:
            transport = StreamableHttpTransport(url=f"http://127.0.0.1:{port}/mcp")
            async with Client(transport=transport) as client:
                return len(await client.list_tools())

        async def both() -> list[int]:
            return list(await asyncio.gather(*(tools(port) for port in ports)))

        assert asyncio.run(both()) == [10, 10]
    finally:
        for proc, _ in servers:
            proc.terminate()
            try:
                proc.wait(timeout=tolerances.PROC_WAIT_S)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
