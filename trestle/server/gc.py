"""Pin-aware retention and garbage collection (R-OPS-1–4)."""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from trestle.server.config import RetentionConfig, TrestleConfig, load_config
from trestle.server.ledger import RunLedger, evidence_dir, ledger_path, work_dir
from trestle.server.pins import PinStore
from trestle.server.recovery import sweep_run_dir


@dataclass(frozen=True)
class GCReport:
    runs_examined: int = 0
    runs_removed: int = 0
    artifacts_collected: int = 0
    snapshots_removed: int = 0
    orphans_swept: int = 0
    bytes_freed: int = 0
    storage_bytes: int = 0
    capped: bool = False


def run_gc(
    home: Path,
    config: TrestleConfig | None = None,
    *,
    now: float | None = None,
) -> GCReport:
    """Sweep expired runs, artifacts, snapshots, and orphan tmp dirs."""
    cfg = config or load_config(home)
    current = time.time() if now is None else now
    pins = PinStore.open(home)
    referenced_snapshots = _referenced_snapshot_ids(home)
    runs_examined = 0
    runs_removed = 0
    artifacts_collected = 0
    orphans_swept = 0
    bytes_freed = 0

    runs_root = home / "runs"
    if runs_root.exists():
        for month_dir in sorted(runs_root.iterdir()):
            if not month_dir.is_dir():
                continue
            for run_dir in sorted(month_dir.iterdir()):
                if not run_dir.is_dir():
                    continue
                path = ledger_path(run_dir)
                if not path.exists():
                    sweep_run_dir(run_dir)
                    orphans_swept += 1
                    continue
                runs_examined += 1
                ledger = RunLedger.open(path)
                if ledger.records:
                    run_id = str(ledger.records[0].get("run_id", run_dir.name))
                else:
                    run_id = run_dir.name
                if pins.is_pinned(run_id):
                    continue
                run_mtime = _run_reference_time(run_dir, ledger, current)
                metadata_age_days = (current - run_mtime) / 86400.0
                if metadata_age_days >= cfg.retention.metadata_days:
                    if _run_has_pinned_artifact(run_dir, ledger, pins):
                        collected, freed = _collect_unpinned_artifacts(
                            run_dir,
                            ledger,
                            pins,
                            cfg.retention,
                            current,
                        )
                        artifacts_collected += collected
                        bytes_freed += freed
                        continue
                    bytes_freed += _dir_size(run_dir)
                    shutil.rmtree(run_dir, ignore_errors=True)
                    runs_removed += 1
                    continue

                collected, freed = _collect_unpinned_artifacts(
                    run_dir,
                    ledger,
                    pins,
                    cfg.retention,
                    current,
                )
                artifacts_collected += collected
                bytes_freed += freed
                orphans_swept += _sweep_orphan_tmp(run_dir)

    snapshots_removed, snap_freed = _gc_snapshots(home, referenced_snapshots)
    bytes_freed += snap_freed
    storage_bytes = _home_storage_bytes(home)
    capped = False
    if storage_bytes > cfg.retention.storage_cap_bytes:
        capped = True
        extra_collected, extra_freed = _enforce_storage_cap(home, cfg.retention, pins, current)
        artifacts_collected += extra_collected
        bytes_freed += extra_freed
        storage_bytes = _home_storage_bytes(home)

    return GCReport(
        runs_examined=runs_examined,
        runs_removed=runs_removed,
        artifacts_collected=artifacts_collected,
        snapshots_removed=snapshots_removed,
        orphans_swept=orphans_swept,
        bytes_freed=bytes_freed,
        storage_bytes=storage_bytes,
        capped=capped,
    )


def count_runs(home: Path) -> dict[str, int]:
    """Summarize run terminal states for doctor output."""
    counts: dict[str, int] = {"total": 0}
    runs_root = home / "runs"
    if not runs_root.exists():
        return counts
    for month_dir in runs_root.iterdir():
        if not month_dir.is_dir():
            continue
        for run_dir in month_dir.iterdir():
            if not run_dir.is_dir():
                continue
            path = ledger_path(run_dir)
            if not path.exists():
                continue
            counts["total"] += 1
            ledger = RunLedger.open(path)
            state = ledger.projected_state()
            counts[state] = counts.get(state, 0) + 1
    return counts


def _referenced_snapshot_ids(home: Path) -> set[str]:
    refs: set[str] = set()
    runs_root = home / "runs"
    if not runs_root.exists():
        return refs
    for month_dir in runs_root.iterdir():
        if not month_dir.is_dir():
            continue
        for run_dir in month_dir.iterdir():
            if not run_dir.is_dir():
                continue
            spec_path = evidence_dir(run_dir) / "spec.json"
            if not spec_path.exists():
                continue
            try:
                spec = json.loads(spec_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            snapshot_id = spec.get("snapshot_id")
            if isinstance(snapshot_id, str) and snapshot_id:
                refs.add(snapshot_id)
    return refs


def _run_reference_time(run_dir: Path, ledger: RunLedger, fallback: float) -> float:
    terminal = ledger.last_kind("execution_ended") or ledger.last_kind("interrupted")
    if terminal is not None:
        at = terminal.get("at")
        if isinstance(at, str):
            try:
                dt = datetime.fromisoformat(at.replace("Z", "+00:00"))
                return dt.timestamp()
            except ValueError:
                pass
    return run_dir.stat().st_mtime if run_dir.exists() else fallback


def _run_has_pinned_artifact(run_dir: Path, ledger: RunLedger, pins: PinStore) -> bool:
    for record in ledger.records:
        if record.get("kind") != "artifact_available":
            continue
        artifact_id = str(record.get("artifact_id", ""))
        if artifact_id and pins.is_pinned(artifact_id):
            return True
    return False


def _collect_unpinned_artifacts(
    run_dir: Path,
    ledger: RunLedger,
    pins: PinStore,
    retention: RetentionConfig,
    now: float,
) -> tuple[int, int]:
    collected = 0
    freed = 0
    run_mtime = _run_reference_time(run_dir, ledger, now)
    artifact_age_days = (now - run_mtime) / 86400.0
    abandoned_ids = _abandoned_artifact_ids(ledger)
    artifacts_dir = evidence_dir(run_dir) / "artifacts"
    if not artifacts_dir.exists():
        return collected, freed
    for record in ledger.records:
        if record.get("kind") != "artifact_available":
            continue
        artifact_id = str(record.get("artifact_id", ""))
        if not artifact_id or pins.is_pinned(artifact_id):
            continue
        path = artifacts_dir / artifact_id
        if not path.exists():
            continue
        if artifact_id in abandoned_ids:
            abandoned_age_hours = (now - run_mtime) / 3600.0
            if abandoned_age_hours < retention.abandoned_hours:
                continue
        elif artifact_age_days < retention.artifact_days:
            continue
        freed += _dir_size(path)
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)
        collected += 1
    return collected, freed


def _abandoned_artifact_ids(ledger: RunLedger) -> set[str]:
    declared = {
        str(item.get("artifact_id", ""))
        for item in ledger.records
        if item.get("kind") == "artifact_declared"
    }
    available = {
        str(item.get("artifact_id", ""))
        for item in ledger.records
        if item.get("kind") == "artifact_available"
    }
    return {artifact_id for artifact_id in declared if artifact_id and artifact_id not in available}


def _sweep_orphan_tmp(run_dir: Path) -> int:
    tmp = work_dir(run_dir) / "tmp"
    if not tmp.exists():
        return 0
    removed = 0
    for path in tmp.iterdir():
        age_hours = (time.time() - path.stat().st_mtime) / 3600.0
        if age_hours >= 24:
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)
            removed += 1
    return removed


def _gc_snapshots(home: Path, referenced: set[str]) -> tuple[int, int]:
    removed = 0
    freed = 0
    snapshots_root = home / "snapshots"
    if not snapshots_root.exists():
        return removed, freed
    for snap_dir in snapshots_root.iterdir():
        if not snap_dir.is_dir():
            continue
        if snap_dir.name in referenced:
            continue
        freed += _dir_size(snap_dir)
        shutil.rmtree(snap_dir, ignore_errors=True)
        removed += 1
    return removed, freed


def _enforce_storage_cap(
    home: Path,
    retention: RetentionConfig,
    pins: PinStore,
    now: float,
) -> tuple[int, int]:
    """Collect oldest unpinned artifact bytes until under cap."""
    candidates: list[tuple[float, Path, str]] = []
    runs_root = home / "runs"
    if not runs_root.exists():
        return 0, 0
    for month_dir in runs_root.iterdir():
        if not month_dir.is_dir():
            continue
        for run_dir in month_dir.iterdir():
            if not run_dir.is_dir():
                continue
            path = ledger_path(run_dir)
            if not path.exists():
                continue
            ledger = RunLedger.open(path)
            artifacts_dir = evidence_dir(run_dir) / "artifacts"
            if not artifacts_dir.exists():
                continue
            for record in ledger.records:
                if record.get("kind") != "artifact_available":
                    continue
                artifact_id = str(record.get("artifact_id", ""))
                if not artifact_id or pins.is_pinned(artifact_id):
                    continue
                artifact_path = artifacts_dir / artifact_id
                if artifact_path.exists():
                    candidates.append((artifact_path.stat().st_mtime, artifact_path, artifact_id))
    candidates.sort(key=lambda item: item[0])
    collected = 0
    freed = 0
    while candidates and _home_storage_bytes(home) > retention.storage_cap_bytes:
        _, artifact_path, _artifact_id = candidates.pop(0)
        if not artifact_path.exists():
            continue
        freed += _dir_size(artifact_path)
        if artifact_path.is_dir():
            shutil.rmtree(artifact_path, ignore_errors=True)
        else:
            artifact_path.unlink(missing_ok=True)
        collected += 1
    return collected, freed


def _dir_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    total = 0
    for child in path.rglob("*"):
        if child.is_file():
            total += child.stat().st_size
    return total


def _home_storage_bytes(home: Path) -> int:
    total = 0
    for root_name in ("runs", "snapshots"):
        root = home / root_name
        if root.exists():
            total += _dir_size(root)
    return total
