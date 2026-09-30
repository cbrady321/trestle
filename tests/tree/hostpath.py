"""The host-path runner for tree tests (MC-B3-02; L.TR-1.7).

Every tree test that needs a tree admitted or refused goes through the two entry points a caller
has: `ControlSurface.run` (`run_tree_via_host`) and the MCP `run` tool on a `trestle serve`
subprocess (`mcp_run_tree`, MC-12). It reaches admission only through them, so a refusal is the
`RequestOutcome` a caller sees and a refused tree leaves no run dir, ledger or process.

It imports nothing from `tests/proof/harness.py` (MC-26's `run_tree`, `admit_tree`, `drive_tree`
write an admitted run directly and bypass admission): the AST check in `test_hostpath.py` holds
that. The publication twins (`publish_tree_via_host`, `mcp_publish_tree`) are the same two entry
points for the grounds that are refused at publication (L.TR-0.4, L.TR-1.5)."""

from __future__ import annotations

from typing import Any

from tests.proof import mcp_host, tolerances
from trestle.common.types import PublishView, RequestOutcome, RunView
from trestle.server.main import Kernel

WAIT_MS = int(tolerances.JOIN_WAIT_S * 1000)


def run_tree_via_host(
    kernel: Kernel,
    plugin: str,
    args: dict[str, Any] | None = None,
    *,
    completion: str = "terminal",
) -> RunView | RequestOutcome:
    """`ControlSurface.run`: a refusal is a `RequestOutcome`, an admitted tree its `RunView`."""
    return kernel.control.run(
        plugin=plugin, args=args or {}, wait_ms=WAIT_MS, completion=completion
    )


def mcp_run_tree(
    host: mcp_host.McpHost, plugin: str, args: dict[str, Any] | None = None
) -> dict[str, Any]:
    """The MCP `run` tool (MC-12) on `host`, waiting for the terminal row: the wire dict, with a
    `code` when the tree was refused and a `run_id` when it was admitted."""
    result = host.call(
        "run",
        {"plugin": plugin, "args": args or {}, "wait_ms": WAIT_MS, "completion": "terminal"},
    )
    assert isinstance(result, dict), result
    return result


def publish_tree_via_host(kernel: Kernel, source: str) -> PublishView | RequestOutcome:
    """`ControlSurface.publish_plugin`."""
    return kernel.control.publish_plugin(source)


def mcp_publish_tree(host: mcp_host.McpHost, source: str) -> dict[str, Any]:
    """The MCP `publish_plugin` tool: the wire dict, with a `code` when publication was refused."""
    result = host.call("publish_plugin", {"source": source})
    assert isinstance(result, dict), result
    return result
