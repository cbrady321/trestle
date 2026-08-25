"""Trestle kernel bootstrap and FastMCP porch."""

from __future__ import annotations

import os
import signal
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from trestle.common.bind import LOOPBACK_HOST
from trestle.common.types import RequestOutcome, RunView, PublishView
from trestle.server.admission import Admission
from trestle.server.conductor import Conductor
from trestle.server.config import load_config
from trestle.server.control import ControlSurface
from trestle.server.project import Project
from trestle.server.plugin_paths import resolve_plugin_dirs
from trestle.server.recovery import recover_on_startup
from trestle.server.registry import Registry
from trestle.server.runs import RunRegistry
from trestle.server.scheduler import Scheduler


def default_home() -> Path:
    return Path(os.environ.get("TRESTLE_HOME", Path.home() / ".trestle"))


@dataclass
class Kernel:
    home: Path
    control: ControlSurface
    registry: Registry


def create_kernel(
    home: Path | None = None,
    plugin_dirs: list[Path] | None = None,
    *,
    cli_plugin_dirs: list[Path] | None = None,
    skip_recovery: bool = False,
) -> Kernel:
    trestle_home = home or default_home()
    if skip_recovery and trestle_home.exists() and (trestle_home / "service_epoch").exists():
        service_epoch = (trestle_home / "service_epoch").read_text(encoding="utf-8").strip()
    else:
        service_epoch = recover_on_startup(trestle_home)
        config = load_config(trestle_home)
        from trestle.server.idempotency import rebuild_from_ledgers

        rebuild_from_ledgers(trestle_home, ttl_s=config.idempotency_ttl_s)

    if plugin_dirs is not None:
        dirs = plugin_dirs
    else:
        dirs = resolve_plugin_dirs(trestle_home, cli_dirs=cli_plugin_dirs)
    registry = Registry(home=trestle_home, plugin_dirs=dirs)
    registry.refresh()
    scheduler = Scheduler()
    run_registry = RunRegistry()
    admission = Admission(
        home=trestle_home,
        registry=registry,
        scheduler=scheduler,
        service_epoch=service_epoch,
    )
    conductor = Conductor(
        home=trestle_home,
        scheduler=scheduler,
        run_registry=run_registry,
    )
    project = Project(
        home=trestle_home,
        registry=registry,
        run_registry=run_registry,
    )
    control = ControlSurface(
        admission=admission,
        project=project,
        conductor=conductor,
        scheduler=scheduler,
    )
    return Kernel(home=trestle_home, control=control, registry=registry)


def _wire_result(value: RequestOutcome | RunView | PublishView | dict[str, Any]) -> dict[str, Any]:
    if isinstance(value, RequestOutcome):
        return value.to_dict()
    if isinstance(value, RunView):
        return value.to_dict()
    if isinstance(value, PublishView):
        return value.to_dict()
    return value


def attach_registry_version_mirror(mcp: Any, kernel: Kernel) -> None:
    """Mirror CatalogView.registry_version on MCP tools/list (R-REG-6)."""
    import mcp.types as mt

    original = mcp._list_tools_mcp

    async def list_tools_with_registry_version(
        request: mt.ListToolsRequest,
    ) -> mt.ListToolsResult:
        kernel.registry.maybe_refresh()
        result = await original(request)
        return mt.ListToolsResult(
            tools=result.tools,
            nextCursor=result.nextCursor,
            _meta={"registry_version": kernel.registry.registry_version},
        )

    mcp._list_tools_mcp = list_tools_with_registry_version


def run_server(
    *,
    transport: str = "stdio",
    port: int = 18732,
    home: Path | None = None,
    cli_plugin_dirs: list[Path] | None = None,
) -> int:
    from fastmcp import FastMCP

    kernel = create_kernel(home=home, cli_plugin_dirs=cli_plugin_dirs)
    mcp = FastMCP("trestle")
    attach_registry_version_mirror(mcp, kernel)

    @mcp.tool
    async def run(
        plugin: str,
        args: dict[str, Any] | None = None,
        version: str | None = None,
        wait_ms: int = 2000,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Start a plugin run and optionally wait for a status frame."""
        kernel.registry.maybe_refresh()
        return _wire_result(
            await kernel.control.run_async(
                plugin=plugin,
                args=args,
                version=version,
                wait_ms=wait_ms,
                idempotency_key=idempotency_key,
            )
        )

    @mcp.tool
    async def await_runs(
        run_ids: list[str],
        mode: str = "all",
        timeout_ms: int = 2000,
    ) -> dict[str, Any] | list[dict[str, Any]]:
        """Wait for existing runs to reach a terminal state."""
        result = await kernel.control.await_runs_async(run_ids, mode=mode, timeout_ms=timeout_ms)
        if isinstance(result, RequestOutcome):
            return result.to_dict()
        return [view.to_dict() for view in result]

    @mcp.tool
    def cancel(run_id: str) -> dict[str, Any]:
        """Request cancellation of a run."""
        return kernel.control.cancel(run_id).to_dict()

    @mcp.tool
    def query(
        view: str,
        params: dict[str, Any] | None = None,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """Query a named view."""
        result = kernel.control.query(view, params, cursor)
        if isinstance(result, RequestOutcome):
            return result.to_dict()
        return result

    @mcp.tool
    def fetch(target: str, window: dict[str, Any]) -> dict[str, Any]:
        """Fetch bytes for a handle within a window."""
        result = kernel.control.fetch(target, window)
        if isinstance(result, RequestOutcome):
            return result.to_dict()
        return result

    @mcp.tool
    def pin(target: str) -> dict[str, Any]:
        """Pin an artifact or run for retention."""
        return kernel.control.pin(target).to_dict()

    @mcp.tool
    def unpin(target: str) -> dict[str, Any]:
        """Remove a retention pin."""
        return kernel.control.unpin(target).to_dict()

    @mcp.tool
    def list_plugins() -> dict[str, Any]:
        """List published plugins."""
        return kernel.control.list_plugins()

    @mcp.tool
    def describe_plugin(plugin_id: str) -> dict[str, Any]:
        """Describe one plugin including input schema."""
        result = kernel.control.describe_plugin(plugin_id)
        if isinstance(result, RequestOutcome):
            return result.to_dict()
        return result

    @mcp.tool
    def publish_plugin(
        source: str,
        name: str | None = None,
    ) -> dict[str, Any]:
        """Publish or update a plugin from Python source at runtime."""
        return _wire_result(kernel.control.publish_plugin(source, name=name))

    def _handle_sigterm(_signum: int, _frame: object | None) -> None:
        kernel.control.scheduler.draining = True

    signal.signal(signal.SIGTERM, _handle_sigterm)

    if transport == "stdio":
        mcp.run(transport="stdio")
    elif transport == "streamable-http":
        mcp.run(transport="streamable-http", host=LOOPBACK_HOST, port=port)
    else:
        raise SystemExit(f"unsupported MCP transport: {transport}")
    return 0
