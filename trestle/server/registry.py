"""Published plugin registry."""

from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from trestle.common import codes
from trestle.common.fsutil import atomic_write, sha256_bytes
from trestle.common.types import CatalogView, PluginCatalogRow, PluginSnapshot, PublishView, RequestOutcome
from trestle.server.plugin_paths import CATALOG_HINT_EMPTY, log_plugin_warning
from trestle.server.snapshots import discover_plugin_name, discover_plugin_name_from_source, materialize_snapshot

MAX_PUBLISH_SOURCE_BYTES = 512 * 1024


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
        claimed: set[str] = set()
        for plugin_dir in self.plugin_dirs:
            if not plugin_dir.is_dir():
                continue
            for path in sorted(plugin_dir.glob("*.py")):
                if path.name.startswith("_"):
                    continue
                plugin_id = discover_plugin_name(path)
                if plugin_id is None:
                    plugin_id = path.stem
                if plugin_id in claimed:
                    log_plugin_warning(
                        self.home,
                        f"duplicate plugin name {plugin_id!r} in {path}; first path wins",
                    )
                    continue
                claimed.add(plugin_id)
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
        search_paths = [str(path) for path in self.plugin_dirs]
        hint = CATALOG_HINT_EMPTY if not items else None
        return CatalogView(
            registry_version=self.registry_version,
            items=items,
            plugin_search_paths=search_paths,
            truncated=False,
            catalog_hint=hint,
        )

    def plugin_counts_by_dir(self) -> dict[Path, int]:
        counts = {plugin_dir: 0 for plugin_dir in self.plugin_dirs}
        claimed: set[str] = set()
        for plugin_dir in self.plugin_dirs:
            if not plugin_dir.is_dir():
                continue
            for path in sorted(plugin_dir.glob("*.py")):
                if path.name.startswith("_"):
                    continue
                plugin_id = discover_plugin_name(path)
                if plugin_id is None:
                    plugin_id = path.stem
                if plugin_id in claimed:
                    continue
                if plugin_id in self.snapshots:
                    counts[plugin_dir] += 1
                    claimed.add(plugin_id)
        return counts

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

    def writable_plugin_dir(self) -> Path:
        if self.plugin_dirs:
            return self.plugin_dirs[0]
        return self.home / "plugins"

    def publish_source(
        self,
        source: str,
        *,
        name: str | None = None,
    ) -> PublishView | RequestOutcome:
        encoded = source.encode("utf-8")
        if len(encoded) > MAX_PUBLISH_SOURCE_BYTES:
            return RequestOutcome(
                code=codes.PUBLICATION_SOURCE_TOO_LARGE,
                message=f"source exceeds {MAX_PUBLISH_SOURCE_BYTES} bytes",
                retryable=False,
                origin="publication",
            )
        try:
            discovered = discover_plugin_name_from_source(source)
        except SyntaxError as exc:
            return RequestOutcome(
                code=codes.PUBLICATION_INVALID_SOURCE,
                message=str(exc)[:200],
                retryable=False,
                origin="publication",
            )
        if discovered is None:
            return RequestOutcome(
                code=codes.PUBLICATION_NO_ENTRYPOINT,
                message="no @trestle entry point found",
                retryable=False,
                origin="publication",
            )
        if name is not None and name != discovered:
            return RequestOutcome(
                code=codes.PUBLICATION_NAME_MISMATCH,
                message=f"name {name!r} does not match entry point {discovered!r}",
                retryable=False,
                origin="publication",
            )
        plugin_name = discovered
        plugin_dir = self.writable_plugin_dir()
        plugin_dir.mkdir(parents=True, exist_ok=True)
        path = plugin_dir / f"{plugin_name}.py"
        created = not path.exists()
        previous = self.snapshots.get(plugin_name)
        source_sha256 = sha256_bytes(encoded)
        atomic_write(path, encoded)
        self.refresh()
        snap = self.get(plugin_name)
        if snap is None or snap.source_sha256 != source_sha256:
            had_previous = previous is not None
            return RequestOutcome(
                code=codes.PUBLICATION_VALIDATION_FAILED,
                message=(
                    "validation failed; previous snapshot still serving"
                    if had_previous
                    else "validation failed; not published"
                ),
                retryable=False,
                origin="publication",
            )
        return PublishView(
            name=plugin_name,
            snapshot_id=snap.snapshot_id,
            registry_version=self.registry_version,
            source_sha256=snap.source_sha256,
            created=created,
        )
