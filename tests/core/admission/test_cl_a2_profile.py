"""CL-A2 the service profile (MC-CORE-07, WR-AUTH-1/2/4/5): `config.toml [profile]` picks `full`
(default: ten tools, any published plugin) or `restricted` (no `publish_plugin`, only allowlisted
plugins, `cancel` scoped to the session that started a run).

Every timing bound comes from `tests.proof.tolerances` (SA-05); no timing literal appears here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

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
