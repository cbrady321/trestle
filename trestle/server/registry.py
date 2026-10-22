"""Published plugin registry."""

from __future__ import annotations

import ast
import math
import os
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from trestle.common import clock, codes
from trestle.common.fsutil import atomic_write, sha256_bytes, sha256_file
from trestle.common.importscan import undeclared_import_warning
from trestle.common.types import (
    CatalogView,
    FailedPluginRow,
    PluginCatalogRow,
    PluginSnapshot,
    PublishView,
    RequestOutcome,
)
from trestle.packs import PackError
from trestle.packs.lock import LockedPack, lock_path, read_lock
from trestle.packs.sources import (
    TIER_PACK,
    TIER_RUNTIME,
    PluginSource,
    SourcePlan,
    plan_sources,
    plugin_files,
    runtime_dir,
    shadowed_pack,
)
from trestle.server.config import load_config
from trestle.server.plugin_paths import (
    CATALOG_HINT_EMPTY,
    CATALOG_HINT_PACKS_MISSING,
    log_plugin_warning,
)
from trestle.server.plugin_schema import (
    EntryError,
    SchemaError,
    declared_from_source,
    find_trestle_function,
    schemas_from_source,
)
from trestle.server.plugin_validate import (
    PluginValidationError,
    PublicationRefused,
    validate_plugin_imports,
)
from trestle.server.snapshots import (
    ORIGIN_AGENT,
    deadline_of,
    discover_plugin_name,
    discover_plugin_name_from_source,
    load_declared,
    load_declared_tree,
    load_snapshot_return_schema,
    load_snapshot_schema,
    materialize_snapshot,
    publication_origin,
    record_agent_publication,
)

MAX_PUBLISH_SOURCE_BYTES = 512 * 1024
# v0.3.1: the home's one registry version counter, bumped under `locks/registry.lock`
REGISTRY_VERSION_FILE = "registry_version"
# how much of a validation failure's message `list_plugins` carries per failed plugin
FAILED_REASON_MAX = 300


def read_registry_version(home: Path) -> int:
    try:
        return int((home / REGISTRY_VERSION_FILE).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0


def bump_registry_version(home: Path) -> int:
    """One counter for the home (v0.3.1), still an int: read, add one and write it back under
    `locks/registry.lock`, so two servers never hand out the same version."""
    from trestle.server.home import REGISTRY_LOCK, file_lock, locks_dir

    with file_lock(locks_dir(home) / REGISTRY_LOCK):
        version = read_registry_version(home) + 1
        atomic_write(home / REGISTRY_VERSION_FILE, f"{version}\n".encode())
    return version


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
    # why the last refresh refused each plugin file that has its own publication code, by path
    _refusals: dict[str, PublicationRefused] = field(default_factory=dict, init=False)
    # the file each published plugin is served from, with its owner and pack (P-1, P-7)
    sources: dict[str, PluginSource] = field(default_factory=dict, init=False)
    # plugin -> the pack plugin it overrides, as `name@version (owner)` (P-11)
    overrides: dict[str, str] = field(default_factory=dict, init=False)
    _lock_error: str | None = field(default=None, init=False, repr=False)
    # every plugin the last refresh could not publish: name -> (source path, reason), which
    # `list_plugins` reports so a caller need not read `service.log` to learn why one is missing
    _failures: dict[str, tuple[str, str]] = field(default_factory=dict, init=False)
    # One refresh at a time. Refreshes come from the admission thread, the `tools/list` hook and
    # the plugin tools, which fastmcp runs on worker threads: unserialized, a refresh that read the
    # plugin directory earlier could overwrite a later one's `snapshots`, and a publish could read
    # back another refresh's result. Re-entrant: `maybe_refresh` and `publish_source` refresh.
    _lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)
    # told the new snapshot set whenever it changes (a server rewrites its lock file's snapshot
    # ids, which GC keeps, v0.3.1)
    on_change: Callable[[dict[str, PluginSnapshot]], None] | None = field(
        default=None, repr=False, compare=False
    )

    def _changed(self, seen: dict[str, PluginSnapshot]) -> None:
        """The plugin set changed: the home's counter moves, and this registry reports it."""
        self.registry_version = bump_registry_version(self.home)
        if self.on_change is not None:
            self.on_change(seen)

    def _packs(self) -> list[LockedPack]:
        """The installed packs (packs.lock). An unreadable lock serves no pack and is logged once
        per distinct error, never guessed at."""
        try:
            packs = read_lock(self.home)
        except PackError as exc:
            if str(exc) != self._lock_error:
                log_plugin_warning(self.home, f"packs.lock ignored: {exc}")
            self._lock_error = str(exc)
            return []
        self._lock_error = None
        return packs

    def _plan(self, name_of: Callable[[Path], str | None]) -> SourcePlan:
        """Every file that may serve each plugin name, in folder order: `[plugins] paths`, the
        installed packs, then the runtime publish folder."""
        return plan_sources(
            self.plugin_dirs, self._packs(), runtime_dir(self.home), name_of=name_of
        )

    def _scan_signature_now(self) -> tuple[tuple[str, int, int], ...]:
        entries: list[tuple[str, int, int]] = []
        try:
            stat = lock_path(self.home).stat()
            # the lock's mtime: an install or removal republishes with no restart
            entries.append((str(lock_path(self.home)), stat.st_mtime_ns, stat.st_ino))
        except FileNotFoundError:
            pass
        packs = self._packs()
        folders = [
            *self.plugin_dirs,
            *(Path(pack.plugins_dir) for pack in packs),
            runtime_dir(self.home),
        ]
        for folder in folders:
            for path in plugin_files(folder):
                try:
                    stat = path.stat()
                except FileNotFoundError:
                    continue  # removed since the listing: it is not there to publish
                entries.append((str(path), stat.st_mtime_ns, stat.st_size))
        for pack in packs:
            if pack.live and pack.library_dir is not None:
                # P-13: a path install's library edit republishes its wrappers, no restart
                entries.extend(library_times(Path(pack.library_dir)))
        return tuple(entries)

    def maybe_refresh(self) -> None:
        with self._lock:
            signature = self._scan_signature_now()
            if signature == self._scan_signature:
                return
            self.refresh()
            self._scan_signature = signature

    def refresh(self) -> None:
        with self._lock:
            self._refresh()

    def _refresh(self) -> None:
        # The directory as this refresh found it: a file that changes while the refresh runs leaves
        # the recorded signature behind, so the next `maybe_refresh` takes the change up.
        signature = self._scan_signature_now()
        failures: dict[str, tuple[str, str]] = {}

        def name_of(path: Path) -> str | None:
            try:
                plugin_id = discover_plugin_name(path)
            except FileNotFoundError:
                return None  # removed since the listing: it is not there to publish
            except SyntaxError as exc:
                log_plugin_warning(self.home, f"plugin {path.name!r} failed validation: {exc}")
                failures.setdefault(path.stem, (str(path), f"syntax error: {exc}"))
                return None
            return plugin_id if plugin_id is not None else path.stem

        plan = self._plan(name_of)
        for name, (served, refused) in plan.collisions.items():
            message = (
                f"plugin {name!r} of pack {refused.pack_label} is not served: pack "
                f"{served.pack_label} provides it too, and neither declares it (P-11)"
            )
            log_plugin_warning(self.home, message)
            failures.setdefault(name, (str(refused.path), message))
        entries = [(source, name) for name, cands in plan.candidates.items() for source in cands]

        seen: dict[str, PluginSnapshot] = {}
        sources: dict[str, PluginSource] = {}
        overrides: dict[str, str] = {}
        refusals: dict[str, PublicationRefused] = {}
        results: dict[Path, PluginSnapshot | None] = {}
        vanished: set[Path] = set()
        errors: dict[Path, str] = {}

        def promote(entry: tuple[PluginSource, str]) -> tuple[Path, PluginSnapshot | None]:
            source, plugin_id = entry
            path = source.path
            self.promotion_budget.acquire()
            try:
                try:
                    snap = materialize_snapshot(
                        path, plugin_id, home=self.home, pack=_pack_stamp(source)
                    )
                except FileNotFoundError:
                    vanished.add(path)  # removed since the listing: not published
                    return path, None
                except (SchemaError, PluginValidationError) as exc:
                    if isinstance(exc, PublicationRefused):
                        refusals[str(path)] = exc
                    log_plugin_warning(
                        self.home,
                        f"plugin {plugin_id!r} failed validation: {exc}",
                    )
                    errors[path] = str(exc)
                    return path, None
                return path, snap
            finally:
                self.promotion_budget.release()

        if entries:
            workers = min(len(entries), self.promotion_budget.max_concurrent)
            with ThreadPoolExecutor(max_workers=workers) as pool:
                for path, snap in pool.map(promote, entries):
                    results[path] = snap

        for plugin_id, candidates in plan.candidates.items():
            failed = next((c for c in candidates if c.path in errors), None)
            if failed is not None:
                failures.setdefault(plugin_id, (str(failed.path), errors[failed.path]))
            # the winner of a name is the first *valid* candidate in folder order, so a broken
            # file never hides a valid one
            winner = next((c for c in candidates if results.get(c.path) is not None), None)
            if winner is None:
                previous = self.snapshots.get(plugin_id)
                if previous is not None and any(c.path not in vanished for c in candidates):
                    seen[plugin_id] = previous
                    if plugin_id in self.sources:
                        sources[plugin_id] = self.sources[plugin_id]
                    if plugin_id in self.overrides:
                        overrides[plugin_id] = self.overrides[plugin_id]
                continue
            snap = results[winner.path]
            assert snap is not None
            for other in candidates:
                if other is not winner and other.tier != TIER_PACK:
                    log_plugin_warning(
                        self.home,
                        f"duplicate plugin name {plugin_id!r} in {other.path}; {winner.path} "
                        "serves it (the first valid file in folder order wins)",
                    )
            seen[plugin_id] = snap
            sources[plugin_id] = winner
            shadow = shadowed_pack(candidates, winner)
            if shadow is not None and shadow.pack_label is not None:
                overrides[plugin_id] = shadow.pack_label
            previous = self.snapshots.get(plugin_id)
            if previous is None or previous.snapshot_id != snap.snapshot_id:
                warning = import_warning(snap)
                if warning is not None:  # R6a: once per newly published snapshot
                    log_plugin_warning(self.home, f"plugin {plugin_id!r} {warning}")
        self._flag_restricted_overrides(overrides, sources)

        changed = seen != self.snapshots or sources != self.sources
        self.snapshots = seen
        self.sources = sources
        self.overrides = overrides
        if changed:
            self._changed(seen)
        self._refusals = refusals
        self._failures = failures
        self._scan_signature = signature

    def _flag_restricted_overrides(
        self, overrides: dict[str, str], sources: dict[str, PluginSource]
    ) -> None:
        """P-14: under the restricted profile, an allowlisted name now served by an override is
        logged (and flagged on its `list_plugins` row), once per change."""
        profile = load_config(self.home).profile
        if not profile.restricted:
            return
        for name, over in sorted(overrides.items()):
            if name not in profile.allowlist or self.overrides.get(name) == over:
                continue
            source = sources[name]
            by = source.pack_label or f"local file {source.path}"
            log_plugin_warning(
                self.home,
                f"restricted profile: allowlisted plugin {name!r} is served by an override "
                f"({by}) over {over}",
            )

    def failure_count(self) -> int:
        """How many plugin files the last refresh could not publish (no import validation)."""
        return len(self._failures)

    def get(self, plugin_id: str) -> PluginSnapshot | None:
        return self.snapshots.get(plugin_id)

    def catalog(self) -> CatalogView:
        self.maybe_refresh()
        items: list[PluginCatalogRow] = []
        packs_missing = False
        for snap in sorted(self.snapshots.values(), key=lambda s: s.plugin):
            import_error = validate_plugin_imports(Path(snap.source_path))
            valid = import_error is None
            if import_error is not None and "trestle_packs" in import_error:
                packs_missing = True
            source = self.sources.get(snap.plugin)
            items.append(
                PluginCatalogRow(
                    name=snap.plugin,
                    version=snap.version,
                    description=f"Plugin {snap.plugin}",
                    valid=valid,
                    capability_class=None,
                    owner=None if source is None else source.owner,
                    pack=None if source is None else source.pack_label,
                    overrides=self.overrides.get(snap.plugin),
                )
            )
        search_paths = [str(path) for path in self.search_folders()]
        hint: str | None = None
        failed = [
            FailedPluginRow(
                name=name,
                source_path=source_path,
                reason=reason[:FAILED_REASON_MAX],
                previous_serving=name in self.snapshots,
            )
            for name, (source_path, reason) in sorted(self._failures.items())
        ]
        if not items:
            hint = CATALOG_HINT_EMPTY
        elif packs_missing:
            hint = CATALOG_HINT_PACKS_MISSING
        return CatalogView(
            registry_version=self.registry_version,
            items=items,
            plugin_search_paths=search_paths,
            truncated=False,
            catalog_hint=hint,
            failed=failed,
        )

    def search_folders(self) -> list[Path]:
        """Every watched folder in order: `[plugins] paths`, the installed packs' wrapper
        folders, the runtime publish folder."""
        return [
            *self.plugin_dirs,
            *(Path(pack.plugins_dir) for pack in self._packs()),
            runtime_dir(self.home),
        ]

    def plugin_counts_by_dir(self) -> dict[Path, int]:
        """How many published plugins each `[plugins] paths` folder serves."""
        counts = {plugin_dir: 0 for plugin_dir in self.plugin_dirs}
        for source in self.sources.values():
            if source.path.parent in counts:
                counts[source.path.parent] += 1
        return counts

    def describe(self, plugin_id: str) -> dict[str, object] | None:
        self.maybe_refresh()
        snap = self.get(plugin_id)
        if snap is None:
            return None
        deadline_s, deadline_source = deadline_of(snap)
        view: dict[str, object] = {
            "name": snap.plugin,
            "version": snap.version,
            "snapshot_id": snap.snapshot_id,
            "source_sha256": snap.source_sha256,
            "summary_budget": snap.summary_budget,
            "timeout_s": math.ceil(deadline_s),
            "deadline_s": deadline_s,
            "deadline_source": deadline_source,
            # The longest a terminal call is held before the caller may give up: the admitted
            # deadline plus the same finalization margin the terminal wait uses (never a config
            # field, MC-B2-04). Discoverable here so a host sizes its own timeout before calling.
            "max_call_duration_s": deadline_s + clock.finalization_margin,
            # v0.3.1 Feature 0: a call may ask for its own `deadline_s` up to the ceiling; a client
            # sizes its timeout for any call as deadline_s + finalization_margin_s (this server's)
            "finalization_margin_s": clock.finalization_margin,
            "deadline_ceiling_s": load_config(self.home).operator_limits.deadline_ceiling,
            # v0.3.1 Feature 2: an interrupted run of this plugin frees its idempotency key
            "repeatable": load_declared(snap).repeatable,
            "input_schema": load_snapshot_schema(snap),
            "return_schema": load_snapshot_return_schema(snap),
        }
        source = self.sources.get(plugin_id)
        if source is not None:
            # P-7: where the wrapper is, read with the agent's own file tools; P-1, P-5: whose
            view["source_path"] = str(source.path)
            view["owner"] = source.owner
            if source.pack_label is not None:
                view["pack"] = source.pack_label
        if plugin_id in self.overrides:
            view["overrides"] = self.overrides[plugin_id]
        declared = load_declared(snap)
        if declared.adapted_from is not None:
            view["adapted_from"] = declared.adapted_from  # P-10: the lineage the plugin declares
        if declared.credentials:
            # change 3: the identities the plugin uses, by name and nothing else about them
            view["credentials"] = list(declared.credentials)
        warning = import_warning(snap)
        if warning is not None:  # R6a: shown, never refused
            view["warnings"] = [warning]
        tree = load_declared_tree(snap)
        if tree is not None:
            # A workflow entry (MC-34) also discloses the outcome codes its declaration can
            # surface, so they are known before invoking (WR-TERM-8); a plain plugin adds nothing.
            view["declared_codes"] = sorted(
                {str(code) for node in tree.nodes.values() for code in node["declared_codes"]}
            )
        return view

    def writable_plugin_dir(self) -> Path:
        """Where `publish_plugin` writes: the runtime folder, the agents' own, never a pack copy
        or the operator's folder."""
        return runtime_dir(self.home)

    def _name_taken(self, plugin_name: str) -> RequestOutcome | Path | None:
        """P-10: a publish under a name served from outside the runtime folder is refused, naming
        the served source and a new name to adapt it under. None: free. A Path: the name is
        served by an agent's own unchanged file in the operator's folder (a publish from before
        the runtime folder existed), which this publish moves to the runtime folder."""
        source = self.sources.get(plugin_name)
        if source is None or source.tier == TIER_RUNTIME:
            return None
        if source.tier != TIER_PACK:
            try:
                digest = sha256_file(source.path)
            except OSError:
                digest = ""
            if publication_origin(self.home, plugin_name, digest) == ORIGIN_AGENT:
                return source.path
        served_by = (
            f"pack {source.pack_label}" if source.pack_label is not None else "the operator's file"
        )
        suggestion = self._free_name(f"{plugin_name}_adapted")
        return RequestOutcome(
            code=codes.PUBLICATION_NAME_TAKEN,
            message=(
                f"{plugin_name} is served by {served_by} and an agent publish cannot replace it. "
                f"Read its source at {source.path} and publish your version as {suggestion}, "
                f'with @trestle(adapted_from="{plugin_name}")'
            )[:600],
            retryable=False,
            origin="publication",
        )

    def _free_name(self, base: str) -> str:
        if base not in self.snapshots:
            return base
        n = 2
        while f"{base}{n}" in self.snapshots:
            n += 1
        return f"{base}{n}"

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
            find_trestle_function(ast.parse(source))
            discovered = discover_plugin_name_from_source(source)
        except EntryError as exc:
            return RequestOutcome(
                code=codes.PUBLICATION_VALIDATION_FAILED,
                message=str(exc)[:200],
                retryable=False,
                origin="publication",
            )
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
        try:
            schemas_from_source(source)
            declared_from_source(source)
        except SchemaError as exc:
            return RequestOutcome(
                code=codes.PUBLICATION_VALIDATION_FAILED,
                message=str(exc)[:200],
                retryable=False,
                origin="publication",
            )
        # The write, its refresh and the read-back are one step: no other refresh lands between
        # them, so the outcome reported is this source's.
        with self._lock:
            self.maybe_refresh()
            taken = self._name_taken(discovered)
            if isinstance(taken, RequestOutcome):
                return taken
            return self._write_and_publish(discovered, encoded, legacy=taken)

    def _write_and_publish(
        self, plugin_name: str, encoded: bytes, *, legacy: Path | None = None
    ) -> PublishView | RequestOutcome:
        plugin_dir = self.writable_plugin_dir()
        plugin_dir.mkdir(parents=True, exist_ok=True)
        path = plugin_dir / f"{plugin_name}.py"
        created = not path.exists()
        previous = self.snapshots.get(plugin_name)
        source_sha256 = sha256_bytes(encoded)
        # change 3: this source came from an agent; its snapshot's manifest says so
        record_agent_publication(self.home, plugin_name, source_sha256)
        atomic_write(path, encoded)
        if legacy is not None:
            # the agent's own earlier publish, unchanged since, moves to the runtime folder
            legacy.unlink(missing_ok=True)
            log_plugin_warning(self.home, f"moved agent publish {legacy} to {path}")
        self.refresh()
        snap = self.get(plugin_name)
        if snap is None or snap.source_sha256 != source_sha256:
            winner = _concurrent_winner(path, source_sha256, snap)
            if winner is not None:
                # v0.3.1: another server wrote this plugin's source after this one did; its source
                # is the one published, not a validation failure of ours
                return RequestOutcome(
                    code=codes.PUBLICATION_REGISTRY_CONFLICT,
                    message=f"{plugin_name} was published concurrently; {winner} won",
                    retryable=True,
                    origin="publication",
                )
            refused = self._refusals.get(str(path))
            if refused is not None:
                return RequestOutcome(
                    code=refused.code,
                    message=str(refused)[:200],
                    retryable=False,
                    origin="publication",
                )
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
            warnings=[w for w in (import_warning(snap),) if w is not None],
        )


def import_warning(snap: PluginSnapshot) -> str | None:
    """R6a: the publication warning for the snapshot's undeclared non-stdlib imports, or None."""
    try:
        source = Path(snap.source_path).read_text(encoding="utf-8")
    except OSError:
        return None
    return undeclared_import_warning(source, load_declared(snap).packages)


def _concurrent_winner(path: Path, ours: str, snap: PluginSnapshot | None) -> str | None:
    """When the source at `path` is no longer the one this publish wrote, the publish that
    replaced it, by snapshot id when the registry already serves it, else by source digest."""
    try:
        on_disk = sha256_file(path)
    except OSError:
        return None
    if on_disk == ours:
        return None
    if snap is not None and snap.source_sha256 == on_disk:
        return f"snapshot {snap.snapshot_id}"
    return f"source sha256 {on_disk[:16]}"


LIBRARY_SKIP_DIRS = frozenset(
    {"__pycache__", "build", "dist", "node_modules", "venv", "tests", "test"}
)


def library_times(root: Path) -> list[tuple[str, int, int]]:
    """P-13: a path install's library files, as (path, mtime, size): the `.py` sources and
    `pyproject.toml`, skipping hidden, build and test folders."""
    out: list[tuple[str, int, int]] = []
    for folder, dirs, files in os.walk(root):
        dirs[:] = sorted(
            d
            for d in dirs
            if not d.startswith(".") and d not in LIBRARY_SKIP_DIRS and not d.endswith(".egg-info")
        )
        for name in sorted(files):
            if not (name.endswith(".py") or name == "pyproject.toml"):
                continue
            path = Path(folder) / name
            try:
                stat = path.stat()
            except FileNotFoundError:
                continue
            out.append((str(path), stat.st_mtime_ns, stat.st_size))
    return out


def _pack_stamp(source: PluginSource) -> dict[str, str] | None:
    """P-1: the pack identity a snapshot's manifest records."""
    if source.tier != TIER_PACK or source.pack is None:
        return None
    pack = source.pack
    return {"name": pack.name, "version": pack.version, "owner": pack.owner}
