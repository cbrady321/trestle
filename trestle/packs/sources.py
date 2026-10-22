"""Where each plugin name may come from, in the order the registry tries them (design § Collisions
and folder order):

1. `[plugins] paths`: the operator's loose files (owner `local`);
2. installed packs, from the lock; a pack that declares `replaces`/`overrides` comes before the
   pack it takes over (P-11), otherwise lock (install) order;
3. `$TRESTLE_HOME/plugins/runtime/`: agent publishes (owner `local`).

The registry validates the candidates and serves the first *valid* one, so a broken file never
hides a valid one. Nothing here imports plugin code: names are read from source.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from trestle.packs.lock import OWNER_LOCAL, LockedPack

RUNTIME_DIR = Path("plugins") / "runtime"

TIER_LOCAL = "local"
TIER_PACK = "pack"
TIER_RUNTIME = "runtime"


def runtime_dir(home: Path) -> Path:
    """The folder `publish_plugin` writes to: the agent's own, never a pack copy or the
    operator's folder."""
    return home / RUNTIME_DIR


@dataclass(frozen=True)
class PluginSource:
    """One file that may serve a plugin name."""

    path: Path
    tier: str
    pack: LockedPack | None = None
    # a pack candidate that comes first because its pack declares the name (P-11)
    declared: bool = False

    @property
    def owner(self) -> str:
        return self.pack.owner if self.pack is not None else OWNER_LOCAL

    @property
    def pack_label(self) -> str | None:
        return None if self.pack is None else self.pack.label


@dataclass
class SourcePlan:
    """Plugin name -> its candidates in precedence order; `collisions` names the undeclared
    pack-to-pack collisions that reached the registry anyway (a hand-edited lock): the later
    pack's file is reported, never served."""

    candidates: dict[str, list[PluginSource]] = field(default_factory=dict)
    collisions: dict[str, tuple[PluginSource, PluginSource]] = field(default_factory=dict)


def plugin_files(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return [p for p in sorted(folder.glob("*.py")) if not p.name.startswith("_")]


def _declares(pack: LockedPack, name: str, others: list[LockedPack]) -> bool:
    return name in pack.overrides or any(pack.replaces == other.name for other in others)


def plan_sources(
    local_dirs: list[Path],
    packs: list[LockedPack],
    runtime: Path | None,
    *,
    name_of: Callable[[Path], str | None],
) -> SourcePlan:
    """`name_of(path)` reads a file's plugin name; None leaves the file out (the caller recorded
    why, a syntax error say)."""
    plan = SourcePlan()
    seen_paths: set[Path] = set()

    def add(source: PluginSource) -> None:
        resolved = source.path.resolve()
        if resolved in seen_paths:
            return  # the same file through two folders (a pack folder also in [plugins] paths)
        seen_paths.add(resolved)
        name = name_of(source.path)
        if name is not None:
            plan.candidates.setdefault(name, []).append(source)

    for folder in local_dirs:
        for path in plugin_files(folder):
            add(PluginSource(path=path, tier=TIER_LOCAL))
    by_name: dict[str, list[PluginSource]] = {}
    for pack in packs:
        for path in plugin_files(Path(pack.plugins_dir)):
            resolved = path.resolve()
            if resolved in seen_paths:
                continue
            seen_paths.add(resolved)
            name = name_of(path)
            if name is not None:
                source = PluginSource(path=path, tier=TIER_PACK, pack=pack)
                by_name.setdefault(name, []).append(source)
    for name, sources in by_name.items():
        providers = [s.pack for s in sources if s.pack is not None]
        ordered: list[PluginSource] = []
        for source in sources:
            assert source.pack is not None
            others = [p for p in providers if p is not source.pack]
            declared = _declares(source.pack, name, others)
            ordered.append(PluginSource(source.path, TIER_PACK, source.pack, declared=declared))
        # declarers first, then install order (a stable sort keeps the lock's order)
        ordered.sort(key=lambda s: not s.declared)
        if len(ordered) > 1 and not ordered[0].declared:
            plan.collisions[name] = (ordered[0], ordered[1])
            ordered = [ordered[0]]
        plan.candidates.setdefault(name, []).extend(ordered)
    if runtime is not None:
        for path in plugin_files(runtime):
            add(PluginSource(path=path, tier=TIER_RUNTIME))
    return plan


def shadowed_pack(candidates: list[PluginSource], winner: PluginSource) -> PluginSource | None:
    """The pack plugin the winner overrides (P-11: shown on the winner's row): the first pack
    candidate after the winner, when the winner is a loose file or a declaring pack."""
    after = candidates[candidates.index(winner) + 1 :]
    if winner.tier == TIER_RUNTIME:
        return None
    if winner.tier == TIER_PACK and not winner.declared:
        return None
    return next((s for s in after if s.tier == TIER_PACK), None)
