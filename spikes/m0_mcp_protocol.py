#!/usr/bin/env python3
"""M0 — MCP 2026-07-28 / FastMCP protocol spike (stdio, list cache, Tasks)."""
from __future__ import annotations

import asyncio
import inspect
import json
import os
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent / "out" / "m0-protocol.json"


def probe_imports() -> dict:
    info: dict = {"python": sys.version, "executable": sys.executable}
    try:
        import fastmcp
        import mcp

        info["fastmcp"] = getattr(fastmcp, "__version__", "?")
        info["mcp"] = getattr(mcp, "__version__", "?")
        info["fastmcp_file"] = inspect.getfile(fastmcp)
    except Exception as e:  # noqa: BLE001
        info["import_error"] = str(e)
        return info

    # Tasks extras
    try:
        import fastmcp_tasks

        info["fastmcp_tasks"] = getattr(fastmcp_tasks, "__version__", "present")
        info["fastmcp_tasks_file"] = inspect.getfile(fastmcp_tasks)
    except Exception as e:  # noqa: BLE001
        info["fastmcp_tasks"] = None
        info["fastmcp_tasks_error"] = str(e)

    try:
        from fastmcp.server.tasks import TasksExtension  # type: ignore

        info["TasksExtension_core"] = True
    except Exception as e:  # noqa: BLE001
        info["TasksExtension_core"] = False
        info["TasksExtension_core_error"] = str(e)

    return info


async def run_inprocess() -> dict:
    from fastmcp import FastMCP, Client

    mcp = FastMCP("trestle-m0")

    @mcp.tool
    def echo(x: str) -> str:
        return x

    @mcp.tool
    async def slow(ms: int = 250) -> str:
        await asyncio.sleep(ms / 1000)
        return "done"

    findings: dict = {}

    async with Client(mcp) as client:
        tools = await client.list_tools()
        findings["list_tools_names"] = [t.name for t in tools]
        findings["list_tools_raw_type"] = type(tools).__name__
        # cache hints
        list_result = None
        if hasattr(client, "list_tools_mcp"):
            list_result = await client.list_tools_mcp()
            findings["list_tools_mcp_type"] = type(list_result).__name__
            findings["list_tools_mcp_dir"] = [x for x in dir(list_result) if not x.startswith("_")][:40]
            dump = {}
            for attr in ("ttlMs", "ttl_ms", "cacheScope", "cache_scope", "meta", "_meta"):
                if hasattr(list_result, attr):
                    dump[attr] = repr(getattr(list_result, attr))[:500]
            if hasattr(list_result, "model_dump"):
                dump["model_dump"] = list_result.model_dump(exclude_none=True)
            findings["list_tools_mcp"] = dump
        r = await client.call_tool("echo", {"x": "hi"})
        findings["call_echo"] = repr(r)[:500]
        r2 = await client.call_tool("slow", {"ms": 200})
        findings["call_slow"] = repr(r2)[:500]

        # session / notifications
        findings["client_transport"] = type(client.transport).__name__ if hasattr(client, "transport") else None

    # Try marking a task tool
    task_probe = {"task_decorator_accepted": False}
    try:
        mcp2 = FastMCP("trestle-m0-task")

        @mcp2.tool(task=True)
        async def background(x: str) -> str:
            await asyncio.sleep(0.05)
            return x

        task_probe["task_decorator_accepted"] = True
        task_probe["startup_note"] = "decorator accepted; serving may still require TasksExtension"
        try:
            async with Client(mcp2) as c2:
                await c2.call_tool("background", {"x": "a"})
                task_probe["call_without_extension"] = "unexpected success"
        except Exception as e:  # noqa: BLE001
            task_probe["call_without_extension"] = f"{type(e).__name__}: {e}"
    except Exception as e:  # noqa: BLE001
        task_probe["task_decorator_error"] = f"{type(e).__name__}: {e}"
    findings["task_probe"] = task_probe
    return findings


async def run_stdio() -> dict:
    from fastmcp import FastMCP, Client
    from fastmcp.client.transports import StdioTransport

    server_src = Path(__file__).resolve().parent / "out" / "_m0_server.py"
    server_src.parent.mkdir(parents=True, exist_ok=True)
    server_src.write_text(
        """
from fastmcp import FastMCP
mcp = FastMCP("trestle-m0-stdio")

@mcp.tool
def ping() -> str:
    return "pong"

if __name__ == "__main__":
    mcp.run(transport="stdio")
"""
    )
    transport = StdioTransport(command=sys.executable, args=[str(server_src)])
    out: dict = {}
    try:
        async with Client(transport=transport) as client:
            tools = await client.list_tools()
            out["stdio_list_tools"] = [t.name for t in tools]
            r = await client.call_tool("ping", {})
            out["stdio_ping"] = repr(r)[:400]
            out["stdio_ok"] = True
    except Exception as e:  # noqa: BLE001
        out["stdio_ok"] = False
        out["stdio_error"] = f"{type(e).__name__}: {e}"
    return out


def inspect_list_changed() -> dict:
    import fastmcp

    root = Path(inspect.getfile(fastmcp)).parent
    hits = []
    for p in root.rglob("*.py"):
        try:
            text = p.read_text(errors="ignore")
        except OSError:
            continue
        if "list_changed" in text or "ttlMs" in text or "ttl_ms" in text:
            hits.append(str(p.relative_to(root)))
    return {"fastmcp_root": str(root), "files_mentioning_list_changed_or_ttl": sorted(set(hits))[:40]}


async def main() -> int:
    report: dict = {"imports": probe_imports()}
    if "import_error" in report["imports"]:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))
        return 1
    report["inprocess"] = await run_inprocess()
    report["stdio"] = await run_stdio()
    report["source_scan"] = inspect_list_changed()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2, default=str))
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
