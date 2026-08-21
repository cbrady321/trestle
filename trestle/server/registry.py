"""Published plugin registry."""

from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from trestle.common.types import CatalogView, PluginCatalogRow, PluginSnapshot
from trestle.server.snapshots import discover_plugin_name, materialize_snapshot


@dataclass
class PromotionBudget:
    """Limit concurrent snapshot materializations during registry refresh."""

    max_concurrent: int = 2
    _semaphore: threading.Semaphore = field(init=False, repr=False)
    _active: int = field(default=0, init=False, repr=False)
    _peak: int = field(default=0, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.max_concurrent < 1:
            msg = "promotion budget must be at least 1"
            raise ValueError(msg)
        self._semaphore = threading.Semaphore(self.max_concurrent)

    def acquire(self) -> None:
        self._semaphore.acquire()
        with self._lock:
            self._active += 1
            if self._active > self._peak:
                self._peak = self._active

    def release(self) -> None:
        with self._lock:
            self._active -= 1
        self._semaphore.release()

    def peak_active(self) -> int:
        with self._lock:
            return self._peak

    def reset_peak(self) -> None:
        with self._lock:
            self._peak = self._active


def default_promotion_budget() -> PromotionBudget:
    raw = os.environ.get("TRESTLE_PROMOTION_BUDGET", "2")
    return PromotionBudget(max_concurrent=max(1, int(raw)))


@dataclass
class Registry:
    home: Path
    plugin_dirs: list[Path]
    snapshots: dict[str, PluginSnapshot] = field(default_factory=dict)
    registry_version: int = 1
    promotion_budget: PromotionBudget = field(default_factory=default_promotion_budget)
    _scan_signature: tuple[tuple[str, int, int], ...] | None = field(default=None, init=False)

    def _scan_signature_now(self) -> tuple[tuple[str, int, int], ...]:
        entries: list[tuple[str, int, int]] = []
        for plugin_dir in self.plugin_dirs:
            if not plugin_dir.is_dir():
                continue
            for path in sorted(plugin_dir.glob("*.py")):
                if path.name.startswith("_"):
                    continue
                stat = path.stat()
                entries.append((str(path), stat.st_mtime_ns, stat.st_size))
        return tuple(entries)

    def maybe_refresh(self) -> None:
        signature = self._scan_signature_now()
        if signature == self._scan_signature:
            return
        self.refresh()
        self._scan_signature = signature

    def refresh(self) -> None:
        paths: list[tuple[Path, str]] = []
        for plugin_dir in self.plugin_dirs:
            if not plugin_dir.is_dir():
                continue
            for path in sorted(plugin_dir.glob("*.py")):
                if path.name.startswith("_"):
                    continue
                plugin_id = discover_plugin_name(path)
                if plugin_id is None:
                    plugin_id = path.stem
                paths.append((path, plugin_id))

        seen: dict[str, PluginSnapshot] = {}
        if not paths:
            if seen != self.snapshots:
                self.registry_version += 1
            self.snapshots = seen
            self._scan_signature = self._scan_signature_now()
            return

        def promote(entry: tuple[Path, str]) -> tuple[str, PluginSnapshot]:
            path, plugin_id = entry
            self.promotion_budget.acquire()
            try:
                snap = materialize_snapshot(path, plugin_id, home=self.home)
            finally:
                self.promotion_budget.release()
            return plugin_id, snap

        workers = min(len(paths), self.promotion_budget.max_concurrent)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for plugin_id, snap in pool.map(promote, paths):
                seen[plugin_id] = snap

        if seen != self.snapshots:
            self.registry_version += 1
        self.snapshots = seen
        self._scan_signature = self._scan_signature_now()

    def get(self, plugin_id: str) -> PluginSnapshot | None:
        return self.snapshots.get(plugin_id)

    def catalog(self) -> CatalogView:
        self.maybe_refresh()
        items = [
            PluginCatalogRow(
                name=snap.plugin,
                version=snap.version,
                description=f"Plugin {snap.plugin}",
                valid=True,
                capability_class=None,
            )
            for snap in sorted(self.snapshots.values(), key=lambda s: s.plugin)
        ]
        return CatalogView(registry_version=self.registry_version, items=items, truncated=False)

    def describe(self, plugin_id: str) -> dict[str, object] | None:
        self.maybe_refresh()
        snap = self.get(plugin_id)
        if snap is None:
            return None
        return {
            "name": snap.plugin,
            "version": snap.version,
            "snapshot_id": snap.snapshot_id,
            "source_sha256": snap.source_sha256,
            "summary_budget": snap.summary_budget,
            "timeout_s": snap.timeout_s,
            "input_schema": {"type": "object", "properties": {}},
        }
