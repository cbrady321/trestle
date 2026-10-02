"""MCP streamable HTTP smoke — E6 transport alongside stdio."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from trestle.common import codes
from trestle.query.catalog import VIEW_CATALOG_URI
from trestle.query.views import FETCH_WINDOW_KINDS, VIEW_NAMES

TOOLS_LIST_BYTE_BUDGET = 8192
REPO = Path(__file__).resolve().parents[1]
HTTP_PORT = 18792


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
        env = os.environ.copy()
        env["TRESTLE_HOME"] = str(smoke_home)

        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "trestle.cli",
                "serve",
                "--transport",
                "streamable-http",
                "--port",
                str(HTTP_PORT),
            ],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        url = f"http://127.0.0.1:{HTTP_PORT}/mcp"
        try:
            for _ in range(50):
                try:
                    transport = StreamableHttpTransport(url=url)
                    async with Client(transport=transport) as client:
                        tools = await client.list_tools()
                        if len(tools) == 10:
                            break
                except Exception:
                    await asyncio.sleep(0.1)
            else:
                stderr = proc.stderr.read().decode() if proc.stderr else ""
                raise AssertionError(f"MCP HTTP server did not become ready: {stderr}")

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
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

    asyncio.run(exercise())
