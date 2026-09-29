"""CL-A2 the service profile (MC-CORE-07, WR-AUTH-1/2/4/5): `config.toml [profile]` picks `full`
(default: ten tools, any published plugin) or `restricted` (no `publish_plugin`, only allowlisted
plugins, `cancel` scoped to the session that started a run).

Every timing bound comes from `tests.proof.tolerances` (SA-05); no timing literal appears here.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from tests.proof import ancestry, mcp_host, tolerances
from trestle.common import codes
from trestle.server.config import PROFILE_RESTRICTED, load_config

FULL_TOOLS = (
    "run",
    "await_runs",
    "cancel",
    "query",
    "fetch",
    "pin",
    "unpin",
    "list_plugins",
    "describe_plugin",
    "publish_plugin",
)
# A plugin that outlives the whole test; the run holding it is cancelled before the test ends.
LONG_S = tolerances.JOIN_WAIT_S * 6
NO_WAIT_MS = 0
# A short join, to show a run has not ended.
GLANCE_MS = int(tolerances.SETTLE_LONG_S * 1000)
# A plugin the operator allowlists, and one that exists but is not on the list.
ALLOWED = "echo"
FORBIDDEN = "slow"
PUBLISHED_NAME = "profile_probe"
PUBLISHED_SOURCE = (
    "from trestle.plugin.surface import Context, trestle\n\n\n"
    "@trestle\n"
    f"def {PUBLISHED_NAME}(ctx: Context) -> dict[str, bool]:\n"
    "    return {'ok': True}\n"
)


def _write_profile(home: Path, mode: str | None, allowlist: list[str] | None = None) -> Path:
    """The operator's config.toml, written before the server starts (the profile is read once)."""
    home.mkdir(parents=True, exist_ok=True)
    lines = []
    if mode is not None:
        lines = ["[profile]", f'mode = "{mode}"']
        if allowlist is not None:
            lines.append("allowlist = [" + ", ".join(f'"{name}"' for name in allowlist) + "]")
    path = home / "config.toml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _tools(host: mcp_host.McpHost) -> list[dict[str, Any]]:
    message = json.loads(host.tools_list_raw())
    tools = message["result"]["tools"]
    assert isinstance(tools, list)
    return tools


def _tool_names(host: mcp_host.McpHost) -> list[str]:
    return [tool["name"] for tool in _tools(host)]


def _run_dirs(home: Path) -> list[Path]:
    return sorted((home / "runs").glob("*/r_*"))


def _server_children(host: mcp_host.McpHost) -> set[ancestry.ProcInfo]:
    return {p for p in ancestry.snapshot() if p.ppid == host.proc.pid}


@pytest.mark.proves(
    "WR-AUTH-1", "WR-AUTH-1:restricted-list-no-publication", "core", "core", "MCP+PROC", "CI"
)
@pytest.mark.proves(
    "WR-AUTH-1", "WR-AUTH-1:publication-through-profile-fails", "core", "core", "MCP+PROC", "CI"
)
@pytest.mark.proves(
    "WR-AUTH-2",
    "WR-AUTH-2:non-allowlisted-no-run-id-no-process",
    "core",
    "core",
    "MCP+PROC",
    "CI",
)
@pytest.mark.proves(
    "WR-AUTH-2", "WR-AUTH-2:intent-cannot-modify-allowlist", "core", "core", "MCP+PROC", "CI"
)
def test_restricted_profile_listing_and_allowlist(tmp_path: Path) -> None:
    home = tmp_path / "host-home"
    config_path = _write_profile(home, PROFILE_RESTRICTED, [ALLOWED])
    config_bytes = config_path.read_bytes()
    with mcp_host.McpHost(home=home) as host:
        # the restricted listing: nine tools, no publish_plugin
        names = _tool_names(host)
        assert names == [name for name in FULL_TOOLS if name != "publish_plugin"], names

        # calling the unregistered tool fails, and nothing is published
        plugins_before = sorted(path.name for path in (home / "plugins").iterdir())
        try:
            published = host.call(
                "publish_plugin", {"source": PUBLISHED_SOURCE, "name": PUBLISHED_NAME}
            )
        except RuntimeError as exc:  # a JSON-RPC error: unknown tool
            assert "publish_plugin" in str(exc), exc
        else:  # an error result: not a PublishView
            assert "plugin_id" not in json.dumps(published), published
            assert "publish_plugin" in json.dumps(published), published
        assert sorted(path.name for path in (home / "plugins").iterdir()) == plugins_before
        listed = host.call("list_plugins")
        assert PUBLISHED_NAME not in json.dumps(listed), listed

        # a non-allowlisted plugin that exists: refused by code, before any run id
        before = _server_children(host)
        refusal = host.call("run", {"plugin": FORBIDDEN, "args": {}, "wait_ms": 0})
        assert refusal["code"] == codes.NOT_ALLOWLISTED, refusal
        assert refusal["origin"] == "admission", refusal
        assert "run_id" not in refusal, refusal
        assert _run_dirs(home) == []
        assert _server_children(host) <= before  # no process was spawned for it

        # an allowlisted one is admitted and its `created` row names the session
        ran = host.call(
            "run",
            {"plugin": ALLOWED, "args": {"message": "hi"}, "wait_ms": tolerances.HARNESS_WAIT_MS},
        )
        assert ran["state"] == "succeeded", ran
        assert len(_run_dirs(home)) == 1

        # intent cannot widen the list: neither `run` arguments nor a plugin-shaped request moves it
        for args in (
            {"allowlist": [FORBIDDEN], "mode": "full", "profile": {"mode": "full"}},
            {"message": "x", "allowlist": [FORBIDDEN]},
        ):
            widened = host.call("run", {"plugin": ALLOWED, "args": args, "wait_ms": 0})
            assert widened["code"] == codes.INVALID_ARGS, widened
        again = host.call("run", {"plugin": FORBIDDEN, "args": {}, "wait_ms": 0})
        assert again["code"] == codes.NOT_ALLOWLISTED, again
        assert len(_run_dirs(home)) == 1
        assert config_path.read_bytes() == config_bytes
        assert _tool_names(host) == names


def test_profile_config_is_validated_and_defaults_to_full(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    default = load_config(home).profile
    assert (default.mode, default.allowlist, default.restricted) == ("full", (), False)
    assert default.allows("anything")

    _write_profile(home, PROFILE_RESTRICTED, [ALLOWED])
    restricted = load_config(home).profile
    assert restricted.restricted and restricted.allows(ALLOWED) and not restricted.allows("other")

    _write_profile(home, PROFILE_RESTRICTED)  # restricted with no allowlist admits nothing
    assert not load_config(home).profile.allows(ALLOWED)

    for bad in ('mode = "lax"', 'mode = "restricted"\nallowlist = "echo"'):
        (home / "config.toml").write_text(f"[profile]\n{bad}\n", encoding="utf-8")
        with pytest.raises(ValueError, match="profile"):
            load_config(home)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextmanager
def _http_server(home: Path) -> Iterator[str]:
    """`trestle serve` over loopback streamable HTTP on `home`; yields the MCP url."""
    port = _free_port()
    env = os.environ.copy()
    env["TRESTLE_HOME"] = str(home)
    proc = subprocess.Popen(
        [sys.executable, "-m", "trestle.cli", "serve", "--transport", "streamable-http"]
        + ["--port", str(port)],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}/mcp"
    try:
        asyncio.run(_wait_ready(url))
        yield url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=tolerances.PROC_WAIT_S)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=tolerances.PROC_WAIT_S)


async def _wait_ready(url: str) -> None:
    deadline = time.monotonic() + tolerances.JOIN_WAIT_S
    while True:
        try:
            async with Client(transport=StreamableHttpTransport(url=url)) as client:
                await client.list_tools()
                return
        except Exception:
            if time.monotonic() >= deadline:
                raise
            await asyncio.sleep(tolerances.POLL_S)


def _seed_plugins(home: Path) -> None:
    (home / "plugins").mkdir(parents=True, exist_ok=True)
    for path in (mcp_host.REPO / "tests" / "fixtures" / "plugins").glob("*.py"):
        (home / "plugins" / path.name).write_bytes(path.read_bytes())


async def _call(client: Client[Any], tool: str, args: dict[str, Any]) -> dict[str, Any]:
    result = await client.call_tool(tool, args)
    assert isinstance(result.data, dict), result
    return result.data


async def _cross_session_cancel(url: str, home: Path) -> tuple[dict[str, Any], ...]:
    """Session A starts a long run; session B cancels it; then A does. Returns B's answer, the
    run's view after B's attempt, A's answer, and the run's final view."""
    transport_a = StreamableHttpTransport(url=url)
    transport_b = StreamableHttpTransport(url=url)
    async with Client(transport=transport_a) as a, Client(transport=transport_b) as b:
        started = await _call(
            a, "run", {"plugin": FORBIDDEN, "args": {"seconds": LONG_S}, "wait_ms": NO_WAIT_MS}
        )
        run_id = started["run_id"]
        try:
            by_b = await _call(b, "cancel", {"run_id": run_id})
            [after_b] = (
                await a.call_tool(
                    "await_runs", {"run_ids": [run_id], "mode": "all", "timeout_ms": GLANCE_MS}
                )
            ).data
            by_a = await _call(a, "cancel", {"run_id": run_id})
            [final] = (
                await a.call_tool(
                    "await_runs",
                    {"run_ids": [run_id], "mode": "all", "timeout_ms": tolerances.HARNESS_WAIT_MS},
                )
            ).data
        finally:
            await a.call_tool("cancel", {"run_id": run_id}, raise_on_error=False)
        return by_b, after_b, by_a, final


def _created_session(home: Path, run_id: str) -> object:
    (run_dir,) = sorted((home / "runs").glob(f"*/{run_id}"))
    rows = [
        json.loads(line)
        for line in (run_dir / "evidence" / "ledger.ndjson").read_text("utf-8").splitlines()
    ]
    (created,) = [row for row in rows if row["kind"] == "created"]
    assert "caller_session" in created, created
    return created["caller_session"]


@pytest.mark.proves(
    "WR-AUTH-1", "WR-AUTH-1:cancel-only-own-session-runs", "core", "core", "MCP", "CI"
)
def test_restricted_cancel_foreign_run_refused(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _seed_plugins(home)
    _write_profile(home, PROFILE_RESTRICTED, [ALLOWED, FORBIDDEN])
    with _http_server(home) as url:
        by_b, after_b, by_a, final = asyncio.run(_cross_session_cancel(url, home))
    # B did not receive the run: refused, and the run went on
    assert by_b["code"] == codes.NOT_OWNER, by_b
    assert by_b["origin"] == "projection", by_b
    assert after_b["state"] in {"queued", "running"}, after_b
    # A did: accepted, and the run ends cancelled
    assert by_a["code"] == codes.CANCEL_ACCEPTED, by_a
    assert final["state"] == "cancelled", final
    # the created row names an MCP session (the key is always there)
    session = _created_session(home, final["run_id"])
    assert isinstance(session, str) and session, session


def test_full_profile_cancel_is_not_session_scoped(tmp_path: Path) -> None:
    home = tmp_path / "home"
    _seed_plugins(home)
    _write_profile(home, None)  # no [profile]: the full profile
    with _http_server(home) as url:
        by_b, _after_b, by_a, final = asyncio.run(_cross_session_cancel(url, home))
    assert by_b["code"] == codes.CANCEL_ACCEPTED, by_b
    assert final["state"] == "cancelled", final
    assert by_a["code"] != codes.NOT_OWNER, by_a


def test_created_row_carries_a_null_session_outside_mcp(tmp_path: Path) -> None:
    from tests.core.spine import support

    kernel = support.spine_kernel(home=tmp_path / "home")
    view = kernel.control.run(plugin="echo", args={"message": "x"}, wait_ms=NO_WAIT_MS)
    assert not hasattr(view, "code"), view
    assert _created_session(kernel.home, view.run_id) is None
