"""Service health, configuration, and recovery."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from trestle.server.config import TrestleConfig, load_config
from trestle.server.gc import GCReport, count_runs, run_gc
from trestle.server.main import create_kernel, default_home
from trestle.server.recovery import recover_on_startup


@dataclass(frozen=True)
class DoctorReport:
    home: Path
    health: str
    service_epoch: str
    registry_version: int
    plugin_count: int
    plugins: tuple[str, ...]
    draining: bool
    run_counts: dict[str, int]
    config: TrestleConfig
    storage_bytes: int
    gc: GCReport | None = None

    def lines(self) -> list[str]:
        rows = [
            f"trestle home: {self.home}",
            f"health: {self.health}",
            f"service_epoch: {self.service_epoch}",
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
        rows.append(f"plugins: {self.plugin_count}")
        for plugin_id in self.plugins:
            rows.append(f"  - {plugin_id}")
        rows.append(f"storage_bytes: {self.storage_bytes}")
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
) -> DoctorReport:
    trestle_home = home
    config = load_config(trestle_home)
    kernel = create_kernel(
        home=trestle_home,
        plugin_dirs=plugin_dirs,
        skip_recovery=skip_recovery,
    )
    kernel.registry.refresh()

    epoch_path = trestle_home / "service_epoch"
    service_epoch = (
        epoch_path.read_text(encoding="utf-8").strip() if epoch_path.exists() else "unknown"
    )
    run_counts = count_runs(trestle_home)
    gc_report = run_gc(trestle_home, config) if run_gc_pass else None
    if gc_report is not None:
        storage_bytes = gc_report.storage_bytes
    else:
        storage_bytes = _storage_bytes(trestle_home)
    health = "ok"
    if run_counts.get("running", 0):
        health = "degraded"

    return DoctorReport(
        home=trestle_home,
        health=health,
        service_epoch=service_epoch,
        registry_version=kernel.registry.registry_version,
        plugin_count=len(kernel.registry.snapshots),
        plugins=tuple(sorted(kernel.registry.snapshots)),
        draining=kernel.control.scheduler.draining,
        run_counts=run_counts,
        config=config,
        storage_bytes=storage_bytes,
        gc=gc_report,
    )


def run_doctor(*, home: str | None = None, run_gc_pass: bool = False) -> int:
    trestle_home = Path(home) if home else default_home()
    report = build_doctor_report(home=trestle_home, run_gc_pass=run_gc_pass)
    for line in report.lines():
        print(line)
    return 0


def run_recover(*, home: str | None = None) -> int:
    trestle_home = Path(home) if home else default_home()
    config = load_config(trestle_home)
    from trestle.server.idempotency import rebuild_from_ledgers

    recover_on_startup(trestle_home)
    rebuild_from_ledgers(trestle_home, ttl_s=config.idempotency_ttl_s)
    gc_report = run_gc(trestle_home, config)
    epoch = (trestle_home / "service_epoch").read_text(encoding="utf-8").strip()
    print(f"trestle home: {trestle_home}")
    print(f"service_epoch: {epoch}")
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
