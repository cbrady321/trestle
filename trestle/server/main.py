"""Trestle kernel bootstrap and MCP surface."""

from __future__ import annotations

import asyncio
import os
import signal
import socket
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from trestle.common.bind import LOOPBACK_HOST
from trestle.common.ids import generate_server_id
from trestle.common.types import PublishView, RequestOutcome, RunView
from trestle.query.catalog import VIEW_CATALOG_URI, view_catalog
from trestle.server.admission import Admission
from trestle.server.conductor import Conductor
from trestle.server.config import ProfileConfig, load_config
from trestle.server.control import ControlSurface
from trestle.server.home import (
    Ownership,
    ServerLock,
    admission_lock,
    check_home,
    raise_nofile_limit,
)
from trestle.server.mcp_schema import FetchWindowArg, QueryViewArg
from trestle.server.plugin_paths import resolve_plugin_dirs
from trestle.server.pool import Pool
from trestle.server.project import Project
from trestle.server.reaper import Reaper
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
    profile: ProfileConfig = ProfileConfig()
    # v0.4 Problem A: this process's server identity (`created.owner`), its owner locks, its
    # reaper and, once it serves, its server lock file
    ownership: Ownership | None = None
    reaper: Reaper | None = None
    server_lock: ServerLock | None = field(default=None, repr=False)

    @property
    def server_id(self) -> str:
        assert self.ownership is not None
        return self.ownership.server_id

    def start_service(self, *, port: int | None = None) -> None:
        """What a serving process adds to its kernel (`trestle serve`, `trestle ops serve`): the
        soft RLIMIT_NOFILE raised to the hard limit, its lock file `home/servers/<id>.lock`
        (naming the snapshots it serves, kept current on every registry change) and the reaper
        thread, every 10 s, woken early by a waiter whose run's owner is gone."""
        if self.server_lock is not None:
            return
        raise_nofile_limit()
        lock = ServerLock.acquire(self.home, self.server_id, port=port)
        self.server_lock = lock
        lock.update(snap.snapshot_id for snap in self.registry.snapshots.values())
        self.registry.on_change = lambda snaps: lock.update(s.snapshot_id for s in snaps.values())
        if self.reaper is not None:
            self.control.project.on_owner_gone = self.reaper.wake
            self.reaper.start()
            self.reaper.wake()  # a first pass now: this server's seen_at (rule 5's reserve)


def create_kernel(
    home: Path | None = None,
    plugin_dirs: list[Path] | None = None,
    *,
    cli_plugin_dirs: list[Path] | None = None,
    skip_recovery: bool = False,
) -> Kernel:
    """Build a kernel on `home`. Before anything else the home must be on a local file system
    and have format 2 (a fresh home is given it; a v0.3.0 home is refused until `trestle init
    --upgrade`). Unless `skip_recovery`, the start runs one reaper pass: only runs whose owner is
    dead are finalized, never a live server's (startup recovery of every run is gone)."""
    trestle_home = home or default_home()
    check_home(trestle_home)
    ownership = Ownership(home=trestle_home, server_id=generate_server_id())
    reaper = Reaper(trestle_home, server_id=ownership.server_id)
    if not skip_recovery:
        reaper.pass_once(scan_debris=True)
        from trestle.server.idempotency import rebuild_from_ledgers

        # Problem B (step 3) makes this a repair only; until then it runs at start, as before,
        # under the admission lock since it rewrites idempotency.json
        with admission_lock(trestle_home):
            rebuild_from_ledgers(trestle_home, ttl_s=load_config(trestle_home).idempotency_ttl_s)

    if plugin_dirs is not None:
        dirs = plugin_dirs
    else:
        dirs = resolve_plugin_dirs(trestle_home, cli_dirs=cli_plugin_dirs)
    registry = Registry(home=trestle_home, plugin_dirs=dirs)
    registry.refresh()
    config = load_config(trestle_home)
    # v0.4 rules 4 and 5: this server's FIFO, bounded per server (read at start), and the home's
    # one slot pool (home/sched.json), whose size is read from config.toml at each grant
    pool = Pool(home=trestle_home, server_id=ownership.server_id)
    scheduler = Scheduler(
        max_running=config.max_running_runs,
        queue_depth=config.queue_depth,
        max_held=config.max_held_runs,
        pool=pool,
    )
    run_registry = RunRegistry()
    admission = Admission(
        home=trestle_home,
        registry=registry,
        scheduler=scheduler,
        ownership=ownership,
        profile=config.profile,
    )

    def complete(run_id: str) -> None:
        # the owner removes a finished run's marker, frees its slot (its home/sched.json entry)
        # and closes its owner lock, in one locked step (v0.4 rules 2 and 7)
        ownership.release(run_id, locked=pool.settle)

    scheduler.on_complete = complete
    # each reaper pass (every 10 s) writes this server's seen_at, the reserve's liveness (rule 5)
    reaper.on_pass = scheduler.seen
    conductor = Conductor(
        home=trestle_home,
        scheduler=scheduler,
        run_registry=run_registry,
    )
    project = Project(
        home=trestle_home,
        registry=registry,
        run_registry=run_registry,
        session_scoped_cancel=config.profile.restricted,
    )
    control = ControlSurface(
        admission=admission,
        project=project,
        conductor=conductor,
        scheduler=scheduler,
    )
    return Kernel(
        home=trestle_home,
        control=control,
        registry=registry,
        profile=config.profile,
        ownership=ownership,
        reaper=reaper,
    )


def _wire_result(value: RequestOutcome | RunView | PublishView | dict[str, Any]) -> dict[str, Any]:
    if isinstance(value, RequestOutcome):
        return value.to_dict()
    if isinstance(value, RunView):
        return value.to_dict()
    if isinstance(value, PublishView):
        return value.to_dict()
    return value


def _caller_session() -> str | None:
    """The MCP session id of the tool call being served, or None outside an MCP session."""
    from fastmcp.server.dependencies import get_context

    try:
        return get_context().session_id
    except RuntimeError:
        return None


def attach_registry_version_mirror(mcp: Any, kernel: Kernel) -> None:
    """Mirror CatalogView.registry_version on MCP tools/list (R-REG-6)."""
    import mcp.types as mt

    original = mcp._list_tools_mcp

    async def list_tools_with_registry_version(
        request: mt.ListToolsRequest,
    ) -> mt.ListToolsResult:
        # the refresh validates dropped-in plugins: it runs on the admission thread, not the loop
        await asyncio.wrap_future(kernel.control.submit_refresh())
        result = await original(request)
        return mt.ListToolsResult(
            tools=result.tools,
            nextCursor=result.nextCursor,
            _meta={"registry_version": kernel.registry.registry_version},
        )

    mcp._list_tools_mcp = list_tools_with_registry_version


LISTENING_PREFIX = "trestle: listening "


def _serve_http_on_a_free_port(mcp: Any) -> None:
    """`serve --port 0` (RACES-REPORT L-18): bind a loopback socket on a port the OS picks, say
    which on stderr (`trestle: listening 127.0.0.1:<port>`) once it accepts connections, and
    serve on that very socket, so a caller never guesses a free port another process may take."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((LOOPBACK_HOST, 0))
    sock.listen(128)
    bound = sock.getsockname()[1]
    print(f"{LISTENING_PREFIX}{LOOPBACK_HOST}:{bound}", file=sys.stderr, flush=True)
    asyncio.run(
        mcp.run_http_async(
            transport="streamable-http", host=LOOPBACK_HOST, port=bound, sockets=[sock]
        )
    )


def run_server(
    *,
    transport: str = "stdio",
    port: int = 18732,
    home: Path | None = None,
    cli_plugin_dirs: list[Path] | None = None,
) -> int:
    from fastmcp import FastMCP

    kernel = create_kernel(home=home, cli_plugin_dirs=cli_plugin_dirs)
    kernel.start_service(port=port if transport == "streamable-http" else None)
    mcp = FastMCP("trestle")
    attach_registry_version_mirror(mcp, kernel)

    @mcp.tool
    async def run(
        plugin: str,
        args: dict[str, Any] | None = None,
        version: str | None = None,
        wait_ms: int = 2000,
        idempotency_key: str | None = None,
        completion: str = "bounded",
    ) -> dict[str, Any]:
        """Start a plugin run and optionally wait for a status frame."""
        return _wire_result(
            await kernel.control.run_async(
                plugin=plugin,
                args=args,
                version=version,
                wait_ms=wait_ms,
                idempotency_key=idempotency_key,
                completion=completion,
                caller_session=_caller_session(),
            )
        )

    @mcp.tool
    async def await_runs(
        run_ids: list[str],
        mode: str = "all",
        timeout_ms: int = 2000,
    ) -> dict[str, Any] | list[dict[str, Any]]:
        """Wait for existing runs to reach a terminal state."""
        result = await kernel.control.await_runs_async(
            run_ids, mode=mode, timeout_ms=timeout_ms, caller_session=_caller_session()
        )
        if isinstance(result, RequestOutcome):
            return result.to_dict()
        return [view.to_dict() for view in result]

    # cancel, query and fetch read the ledger and evidence files: they run on a worker thread so a
    # held call (or a slow read) never stops the loop answering the others (L.CS-4.2).
    @mcp.tool
    async def cancel(run_id: str) -> dict[str, Any]:
        """Request cancellation of a run."""
        outcome = await asyncio.to_thread(kernel.control.cancel, run_id, _caller_session())
        return outcome.to_dict()

    @mcp.tool
    async def query(
        view: QueryViewArg,
        params: dict[str, Any] | None = None,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """Query a named view. When to pick each view: trestle://views."""
        result = await asyncio.to_thread(kernel.control.query, view, params, cursor)
        if isinstance(result, RequestOutcome):
            return result.to_dict()
        return result

    @mcp.tool
    async def fetch(target: str, window: FetchWindowArg) -> dict[str, Any]:
        """Fetch bytes for a handle within a window. When to pick: trestle://views."""
        result = await asyncio.to_thread(kernel.control.fetch, target, window)
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
        """Describe one plugin including input and return schema."""
        result = kernel.control.describe_plugin(plugin_id)
        if isinstance(result, RequestOutcome):
            return result.to_dict()
        return result

    def publish_plugin(
        source: str,
        name: str | None = None,
    ) -> dict[str, Any]:
        """Publish or update a plugin from Python source at runtime."""
        return _wire_result(kernel.control.publish_plugin(source, name=name))

    # The restricted profile does not register the tool at all: it is neither listed nor callable.
    if not kernel.profile.restricted:
        mcp.tool(publish_plugin)

    @mcp.resource(
        VIEW_CATALOG_URI,
        name="view_catalog",
        description="When to pick each named query view versus fetch (not plugin schemas).",
        mime_type="application/json",
    )
    def view_catalog_resource() -> dict[str, Any]:
        return view_catalog()

    def _handle_sigterm(_signum: int, _frame: object | None) -> None:
        # D4: a draining server refuses new runs and keeps starting and finishing what it
        # admitted; it never exits on SIGTERM (the operator stops it once its live runs are 0)
        kernel.control.scheduler.draining = True

    signal.signal(signal.SIGTERM, _handle_sigterm)

    if transport == "stdio":
        mcp.run(transport="stdio")
    elif transport == "streamable-http":
        if port == 0:
            _serve_http_on_a_free_port(mcp)
        else:
            mcp.run(transport="streamable-http", host=LOOPBACK_HOST, port=port)
    else:
        raise SystemExit(f"unsupported MCP transport: {transport}")
    return 0
