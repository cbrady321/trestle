"""What `trestle pack list` shows, and the grants an install left stale. Read from the lock and
the sources (no plugin import, no validation): the registry's own answer is `list_plugins`."""

from __future__ import annotations

from pathlib import Path

from trestle.packs.bundled import available_bundled
from trestle.packs.lock import read_lock
from trestle.packs.sources import plan_sources, runtime_dir, shadowed_pack


def source_name(path: Path) -> str | None:
    """A file's plugin name as the registry reads it: the `@trestle` function's, else the stem;
    None for a file that does not parse."""
    from trestle.server.snapshots import discover_plugin_name

    try:
        return discover_plugin_name(path) or path.stem
    except (OSError, SyntaxError, UnicodeDecodeError):
        return None


def overview(home: Path) -> dict[str, object]:
    from trestle.server.config import load_config
    from trestle.server.plugin_paths import resolve_plugin_dirs

    packs = read_lock(home)
    profile = load_config(home).profile
    plan = plan_sources(
        resolve_plugin_dirs(home, cli_dirs=None), packs, runtime_dir(home), name_of=source_name
    )
    overrides: list[dict[str, object]] = []
    for name, candidates in sorted(plan.candidates.items()):
        winner = candidates[0]
        shadow = shadowed_pack(candidates, winner)
        if shadow is None:
            continue
        overrides.append(
            {
                "plugin": name,
                "by": winner.pack_label or f"local file {winner.path}",
                "over": shadow.pack_label,
                # P-14: under the restricted profile an allowlisted name served by an override
                "flagged": profile.restricted and name in profile.allowlist,
            }
        )
    collisions = [
        {"plugin": name, "served_by": first.pack_label, "refused": second.pack_label}
        for name, (first, second) in sorted(plan.collisions.items())
    ]
    installed = {pack.name for pack in packs}
    return {
        "installed": [pack.to_dict() | {"label": pack.label} for pack in packs],
        "overrides": overrides,
        "collisions": collisions,
        "available": sorted(set(available_bundled()) - installed),
    }


def stale_grants(home: Path) -> list[tuple[str, str]]:
    """(identity, plugin) for each grant to an installed pack's plugin that no longer matches the
    plugin as published now (Decision 2: grants pin digests, so an upgrade drops them)."""
    from trestle.server.credentials import GRANT_STALE, grant_status, load_credentials

    pack_plugins = {name for pack in read_lock(home) for name in pack.plugins}
    config = load_credentials(home)
    grants = [
        (ident.name, grant)
        for ident in config.identities.values()
        for grant in ident.grants
        if grant.plugin in pack_plugins
    ]
    if not grants:
        return []
    from trestle.server.credentials_cli import published_plugins

    published = published_plugins(home)
    out: list[tuple[str, str]] = []
    for name, grant in grants:
        found = published.get(grant.plugin)
        current = None if found is None else (found.source_sha256, found.package_digests)
        if grant_status(grant, current) == GRANT_STALE:
            out.append((name, grant.plugin))
    return out
