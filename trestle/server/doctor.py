"""Service health, configuration, and recovery."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from trestle.server.config import IGNORED_MAX_RUNNING_ENV, TrestleConfig, load_config
from trestle.server.gc import GCReport, count_runs, run_gc
from trestle.server.home import (
    admission_holder,
    admission_lock,
    check_home,
    home_format,
    live_run_ids,
    live_servers,
    read_marker,
)
from trestle.server.main import create_kernel, default_home
from trestle.server.plugin_paths import resolve_plugin_dirs
from trestle.server.reaper import reap_home


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
    # v0.4: the live servers (their lock files), the live runs by owner (None: a v0.3.0 run),
    # the admission lock's holder when it is held, and warnings (an ignored environment override)
    servers: tuple[dict[str, Any], ...] = ()
    live_runs: dict[str | None, int] = field(default_factory=dict)
    admission_holder: str | None = None
    warnings: tuple[str, ...] = ()

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
        if self.admission_holder is not None:
            rows.append(f"admission_lock: held by {self.admission_holder}")
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
    if run_counts.get("running", 0):
        health = "degraded"
    counts_by_dir = kernel.registry.plugin_counts_by_dir()

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
        warnings=_warnings(),
    )


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
) -> int:
    trestle_home = Path(home) if home else default_home()
    report = build_doctor_report(
        home=trestle_home,
        run_gc_pass=run_gc_pass,
        cli_plugin_dirs=cli_plugin_dirs,
    )
    for line in report.lines():
        print(line)
    return 0


def run_recover(*, home: str | None = None) -> int:
    """`trestle recover`: reap now, then GC (v0.4). Only runs whose owner lock is free (their
    server died) are finalized; a live server's runs are never touched."""
    trestle_home = Path(home) if home else default_home()
    check_home(trestle_home)
    config = load_config(trestle_home)
    from trestle.server.idempotency import rebuild_from_ledgers

    reaped = reap_home(trestle_home)
    with admission_lock(trestle_home):  # Problem B makes the rebuild a repair (doctor)
        rebuild_from_ledgers(trestle_home, ttl_s=config.idempotency_ttl_s)
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
