"""WR-COMPAT-2 pin: plugins are not tools. The raw `tools/list` a stdio MCP
host sees is the same whether one plugin or twenty are published."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.proof.mcp_host import McpHost

FEW = 1
MANY = 20

PLUGIN_SOURCE = """\
from trestle.plugin.surface import Context, trestle


@trestle
def plugin_{n:02d}(ctx: Context, value: str = "ok") -> dict[str, str]:
    return {{"value": value}}
"""


class _OwnPluginsHost(McpHost):
    """An `McpHost` that serves only the plugins its test wrote, not the
    shared fixture set the base host seeds."""

    def _seed_fixture_plugins(self) -> None:
        return None


def _normalized_tools_list(home: Path, plugin_count: int) -> tuple[str, int]:
    plugins = home / "plugins"
    plugins.mkdir(parents=True)
    for n in range(plugin_count):
        (plugins / f"plugin_{n:02d}.py").write_text(PLUGIN_SOURCE.format(n=n), encoding="utf-8")
    host = _OwnPluginsHost(home=home)
    try:
        raw = host.tools_list_raw()
        published = len(host.call("list_plugins", {})["items"])
    finally:
        host.close()
    result = json.loads(raw)["result"]
    result.pop("_meta", None)  # registry_version legitimately tracks the plugin set
    return json.dumps(result, sort_keys=True, separators=(",", ":")), published


@pytest.mark.compat
@pytest.mark.proves("WR-COMPAT-2", "WR-COMPAT-2:preserved", "core", "core", "PROC", "CI")
def test_tools_list_independent_of_plugin_count(tmp_path: Path) -> None:
    few, few_published = _normalized_tools_list(tmp_path / "few", FEW)
    many, many_published = _normalized_tools_list(tmp_path / "many", MANY)
    assert (few_published, many_published) == (FEW, MANY)
    assert few.encode("utf-8") == many.encode("utf-8")
