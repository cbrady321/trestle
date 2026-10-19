"""SA-12 drift proof: the raw HTTP host's view of `tools/list` and of its
own request count agrees with the fastmcp client's independent view
(L.P0-0b.2)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastmcp import Client

from tests.proof import mcp_host
from tests.proof.mcp_host import McpHost

REPO = Path(__file__).resolve().parents[3]


def _fastmcp_tools_view(home: Path) -> list[dict[str, object]]:
    async def exercise(port: int) -> list[dict[str, object]]:
        async with Client(transport=mcp_host.http_transport(home, port)) as client:
            tools = await client.list_tools()
            return [
                {"name": t.name, "inputSchema": t.inputSchema}
                for t in sorted(tools, key=lambda t: t.name)
            ]

    proc, port = mcp_host.serve_http(home)
    try:
        return asyncio.run(exercise(port))
    finally:
        mcp_host.stop_http(proc)


@pytest.mark.parametrize("sa", ["SA-12"])
def test_host_tools_list_equals_fastmcp_client(sa: str, tmp_path: Path) -> None:
    host = McpHost(home=tmp_path / "host-home")
    try:
        raw = host.tools_list_raw()
        message = json.loads(raw)
        host_tools = sorted(
            (
                {"name": t["name"], "inputSchema": t["inputSchema"]}
                for t in message["result"]["tools"]
            ),
            key=lambda t: t["name"],
        )
    finally:
        host.close()

    client_view = _fastmcp_tools_view(tmp_path / "client-home")
    assert host_tools == client_view


@pytest.mark.parametrize("sa", ["SA-12"])
def test_request_count_exact(sa: str, tmp_path: Path) -> None:
    host = McpHost(home=tmp_path / "count-home")
    try:
        # only "initialize" is a request (has an id); "notifications/initialized" is not
        assert host.request_count() == 1
        host.tools_list_raw()
        assert host.request_count() == 2
        host.call("list_plugins", {})
        assert host.request_count() == 3
        host.call("run", {"plugin": "echo", "args": {"message": "count"}, "wait_ms": 5000})
        assert host.request_count() == 4
    finally:
        host.close()
