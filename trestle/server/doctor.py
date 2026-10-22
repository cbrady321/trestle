"""Service health, configuration, and recovery."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from trestle.common.types import PluginSnapshot
from trestle.server.config import IGNORED_MAX_RUNNING_ENV, TrestleConfig, load_config
from trestle.server.credential_store import StoreError
from trestle.server.credentials import GRANT_CURRENT, grant_status, load_credentials
from trestle.server.gc import GCReport, count_runs, run_gc
from trestle.server.home import (
    admission_holder,
    check_home,
    home_format,
    live_run_ids,
    live_servers,
    marked_run_dir,
    read_marker,
)
from trestle.server.ledger import TERMINAL_KINDS, read_state
from trestle.server.main import create_kernel, default_home
from trestle.server.mcp_sessions import SERVER_GONE, SessionReader, owner_running
from trestle.server.plugin_paths import resolve_plugin_dirs
from trestle.server.reaper import reap_home
from trestle.server.snapshots import load_declared


@dataclass(frozen=True)
class DoctorReport:
    home: Path
    health: str
    home_format: int | None
    registry_version: int
    plugin_count: int
    plugins: tuple[str, ...]
    plugin_search_paths: tuple[tuple[str, int], ...]
    draining: bool
    run_counts: dict[str, int]
    config: TrestleConfig
    storage_bytes: int
    gc: GCReport | None = None
    # v0.3.1: the live servers (their lock files), the live runs by owner (None: a v0.3.0 run),
    # the admission lock's holder when it is held, and warnings (an ignored environment override)
    servers: tuple[dict[str, Any], ...] = ()
    live_runs: dict[str | None, int] = field(default_factory=dict)
    admission_holder: str | None = None
    warnings: tuple[str, ...] = ()
    # watched folders that do not exist: shown as missing, not as holding 0 plugins
    missing_plugin_dirs: tuple[str, ...] = ()
    # change 3: the credential identities (names, env, grant status, the store), never a value
    credentials: tuple[str, ...] = ()
    # change 2a: the live runs whose MCP session has ended, one line each (at most
    # SESSION_ENDED_LISTED), and how many there are
    session_ended_runs: tuple[str, ...] = ()
    session_ended_count: int = 0

    def lines(self) -> list[str]:
        rows = [
            f"trestle home: {self.home}",
            f"health: {self.health}",
            f"home_format: {self.home_format}",
            f"registry_version: {self.registry_version}",
            f"draining: {'true' if self.draining else 'false'}",
            "config:",
            f"  metadata_retention_days: {self.config.retention.metadata_days}",
            f"  artifact_retention_days: {self.config.retention.artifact_days}",
            f"  abandoned_retention_hours: {self.config.retention.abandoned_hours}",
            f"  storage_cap_bytes: {self.config.retention.storage_cap_bytes}",
            f"  idempotency_ttl_s: {self.config.idempotency_ttl_s}",
            f"  service_log: {self.config.service_log}",
            "runs:",
            f"  total: {self.run_counts.get('total', 0)}",
        ]
        for state in sorted(k for k in self.run_counts if k != "total"):
            rows.append(f"  {state}: {self.run_counts[state]}")
        rows.append("plugin_search_paths:")
        for path, count in self.plugin_search_paths:
            if path in self.missing_plugin_dirs:
                rows.append(f"  - {path} (missing)")
            else:
                rows.append(f"  - {path} ({count} plugins)")
        rows.append(f"plugins: {self.plugin_count}")
        for plugin_id in self.plugins:
            rows.append(f"  - {plugin_id}")
        rows.append(f"storage_bytes: {self.storage_bytes}")
        rows.append(f"servers: {len(self.servers)}")
        for server in self.servers:
            owned = self.live_runs.get(str(server.get("server_id")), 0)
            rows.append(
                f"  - {server.get('server_id')} pid={server.get('pid')} "
                f"port={server.get('port')} live_runs={owned}"
            )
        rows.append(f"live_runs: {sum(self.live_runs.values())}")
        for owner in sorted(self.live_runs, key=lambda o: (o is None, o or "")):
            rows.append(f"  {owner or 'v0.3.0 (no owner)'}: {self.live_runs[owner]}")
        rows.append(f"session_ended_runs: {self.session_ended_count}")
        rows.extend(f"  - {line}" for line in self.session_ended_runs)
        if self.session_ended_count > len(self.session_ended_runs):
            rows.append(f"  … and {self.session_ended_count - len(self.session_ended_runs)} more")
        if self.admission_holder is not None:
            rows.append(f"admission_lock: held by {self.admission_holder}")
        rows.extend(self.credentials)
        for warning in self.warnings:
            rows.append(f"warning: {warning}")
        if self.gc is not None:
            rows.extend(
                [
                    "gc:",
                    f"  runs_examined: {self.gc.runs_examined}",
                    f"  runs_removed: {self.gc.runs_removed}",
                    f"  artifacts_collected: {self.gc.artifacts_collected}",
                    f"  snapshots_removed: {self.gc.snapshots_removed}",
                    f"  bytes_freed: {self.gc.bytes_freed}",
                ]
            )
        return rows


def build_doctor_report(
    *,
    home: Path,
    run_gc_pass: bool = False,
    skip_recovery: bool = True,
    plugin_dirs: list[Path] | None = None,
    cli_plugin_dirs: list[Path] | None = None,
) -> DoctorReport:
    trestle_home = home
    check_home(trestle_home)
    config = load_config(trestle_home)
    resolved_dirs = plugin_dirs
    if resolved_dirs is None:
        resolved_dirs = resolve_plugin_dirs(trestle_home, cli_dirs=cli_plugin_dirs)
    kernel = create_kernel(
        home=trestle_home,
        plugin_dirs=resolved_dirs,
        skip_recovery=skip_recovery,
    )
    kernel.registry.refresh()

    run_counts = count_runs(trestle_home)
    gc_report = run_gc(trestle_home, config) if run_gc_pass else None
    if gc_report is not None:
        storage_bytes = gc_report.storage_bytes
    else:
        storage_bytes = _storage_bytes(trestle_home)
    health = "ok"
    # a run in flight, a held one included (Feature 3), degrades health
    if any(run_counts.get(state, 0) for state in ("running", "held")):
        health = "degraded"
    counts_by_dir = kernel.registry.plugin_counts_by_dir()
    missing_dirs = [path for path in kernel.registry.plugin_dirs if not path.is_dir()]
    # no watched folder exists: the server has nowhere to find plugins
    if missing_dirs and len(missing_dirs) == len(kernel.registry.plugin_dirs):
        health = "degraded"

    ended_lines, ended_count = session_ended_report(trestle_home)
    credential_rows, credential_warnings, store_ok = credentials_report(
        trestle_home, kernel.registry.snapshots
    )
    if not store_ok:
        health = "degraded"

    return DoctorReport(
        home=trestle_home,
        health=health,
        home_format=home_format(trestle_home),
        registry_version=kernel.registry.registry_version,
        plugin_count=len(kernel.registry.snapshots),
        plugins=tuple(sorted(kernel.registry.snapshots)),
        plugin_search_paths=tuple(
            (str(path), counts_by_dir.get(path, 0)) for path in kernel.registry.plugin_dirs
        ),
        draining=kernel.control.scheduler.draining,
        run_counts=run_counts,
        config=config,
        storage_bytes=storage_bytes,
        gc=gc_report,
        servers=tuple(live_servers(trestle_home)),
        live_runs=_live_runs_by_owner(trestle_home),
        admission_holder=admission_holder(trestle_home),
        warnings=_warnings() + credential_warnings,
        missing_plugin_dirs=tuple(str(path) for path in missing_dirs),
        credentials=credential_rows,
        session_ended_runs=ended_lines,
        session_ended_count=ended_count,
    )


SESSION_ENDED_LISTED = 20


def session_ended_report(home: Path) -> tuple[tuple[str, ...], int]:
    """Change 2a: the live runs (their markers, then each run's state.json) whose MCP session has
    ended: at most `SESSION_ENDED_LISTED` lines and the count. Health is unchanged: such a run is a
    live run, and live runs already degrade it."""
    reader = SessionReader(home)
    lines: list[str] = []
    count = 0
    for run_id in live_run_ids(home):
        run_dir = marked_run_dir(home, run_id, read_marker(home, run_id))
        state = read_state(run_dir) if run_dir is not None else None
        if state is None or state.get("state") in TERMINAL_KINDS:
            continue
        alive = owner_running(run_dir)
        end = reader.end(state.get("owner"), state.get("caller_session"), owner_alive=alive)
        if end is None:
            continue
        count += 1
        if len(lines) >= SESSION_ENDED_LISTED:
            continue
        why = (
            "server gone"
            if end.reason == SERVER_GONE
            else f"session {end.reason} at {end.at or 'an unknown time'}"
        )
        lines.append(f"{run_id} {state.get('plugin')} {state.get('state')}: {why}")
    return tuple(lines), count


def credentials_report(
    home: Path, snapshots: Mapping[str, PluginSnapshot]
) -> tuple[tuple[str, ...], tuple[str, ...], bool]:
    """The doctor's credentials section: (rows, warnings, store readable). Identities and their
    grants' status against the plugins published now, and whether the store and its key read;
    never a value. Nothing configured: no rows."""
    config = load_credentials(home)
    if not config.identities:
        return ("credentials: 0 identities",), (), True
    rows = [f"credentials: {len(config.identities)} identities"]
    warnings: list[str] = []
    for name in sorted(config.identities):
        ident = config.identities[name]
        rows.append(f"  - {name} (kind={ident.kind}, env={ident.env})")
        for grant in sorted(ident.grants, key=lambda g: g.plugin):
            snap = snapshots.get(grant.plugin)
            current = (
                (snap.source_sha256, dict(load_declared(snap).package_digests))
                if snap is not None
                else None
            )
            status = grant_status(grant, current)
            rows.append(f"      grant {grant.plugin}: {status}")
            if status != GRANT_CURRENT:
                warnings.append(
                    f"credential {name} grant to {grant.plugin} is {status}; "
                    f"`trestle credentials grant {name} {grant.plugin}` re-pins it"
                )
    store = config.store(home)
    try:
        stored = store.read_all()
    except StoreError as exc:
        rows.append(f"  store: unreadable ({exc}); key: {store.provider.describe()}")
        warnings.append(f"the credential store could not be read: {exc}")
        return tuple(rows), tuple(warnings), False
    rows.append(f"  store: readable; key: {store.provider.describe()}")
    for name in sorted(set(config.identities) - set(stored)):
        warnings.append(
            f"credential {name} has no stored value: `trestle credentials add {name} --replace`"
        )
    return tuple(rows), tuple(warnings), True


def _live_runs_by_owner(home: Path) -> dict[str | None, int]:
    counts: dict[str | None, int] = {}
    for run_id in live_run_ids(home):
        owner = (read_marker(home, run_id) or {}).get("owner")
        key = owner if isinstance(owner, str) else None
        counts[key] = counts.get(key, 0) + 1
    return counts


def _warnings() -> tuple[str, ...]:
    found: list[str] = []
    if os.environ.get(IGNORED_MAX_RUNNING_ENV) is not None:
        found.append(
            f"{IGNORED_MAX_RUNNING_ENV} is set and ignored: every server on a home shares one "
            "pool, sized by max_running_runs in config.toml"
        )
    return tuple(found)


def run_doctor(
    *,
    home: str | None = None,
    run_gc_pass: bool = False,
    cli_plugin_dirs: list[Path] | None = None,
    rebuild_keys: bool = False,
) -> int:
    trestle_home = Path(home) if home else default_home()
    report = build_doctor_report(
        home=trestle_home,
        run_gc_pass=run_gc_pass,
        cli_plugin_dirs=cli_plugin_dirs,
    )
    for line in report.lines():
        print(line)
    if rebuild_keys:
        # Problem B's repair: home/keys/ replayed from the created rows (an expired key stays
        # expired), one key at a time under the admission lock
        from trestle.server.idempotency import rebuild_keys as rebuild

        rewritten = rebuild(trestle_home, ttl_s=load_config(trestle_home).idempotency_ttl_s)
        print(f"keys rebuilt: {rewritten}")
    return 0


def run_recover(*, home: str | None = None) -> int:
    """`trestle recover`: reap now, then GC (v0.3.1). Only runs whose owner lock is free (their
    server died) are finalized; a live server's runs are never touched."""
    trestle_home = Path(home) if home else default_home()
    check_home(trestle_home)
    config = load_config(trestle_home)
    reaped = reap_home(trestle_home)
    gc_report = run_gc(trestle_home, config)
    print(f"trestle home: {trestle_home}")
    print(f"reaped: {len(reaped.reaped)}")
    print(f"live runs left: {reaped.left_live}")
    print("recovery complete")
    print(f"gc runs_removed: {gc_report.runs_removed}")
    print(f"gc artifacts_collected: {gc_report.artifacts_collected}")
    return 0


def _storage_bytes(home: Path) -> int:
    total = 0
    for root_name in ("runs", "snapshots"):
        root = home / root_name
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.is_file():
                total += path.stat().st_size
    return total
