"""`pack.toml`, the one file that makes a folder a pack.

    [pack]
    name = "github"                  # [a-z][a-z0-9_]*
    summary = "..."
    library = "."                    # optional: the directory holding pyproject.toml
    replaces = "github"              # optional (P-11): takes over a whole installed pack
    overrides = ["github_pr_open"]   # optional (P-11): takes over single plugins
    [requires]
    tools = ["gh", "git"]            # optional: checked on the service PATH at install
    packs = ["docker"]               # optional (P-16): installed from the same release
    [config]
    files = ["TRESTLE_GITHUB_POLICY"]  # optional (P-20): variables naming configuration files

Unknown tables and keys are refused, so a typo is never silently ignored.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from trestle.packs import PackError

MANIFEST_FILE = "pack.toml"
PLUGINS_DIR = "plugins"
PACK_NAME = re.compile(r"[a-z][a-z0-9_]*\Z")
PLUGIN_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
ENV_NAME = re.compile(r"[A-Z_][A-Z0-9_]*\Z")

_KEYS = {
    "pack": {"name", "summary", "library", "replaces", "overrides"},
    "requires": {"tools", "packs"},
    "config": {"files"},
}


@dataclass(frozen=True)
class PackManifest:
    name: str
    summary: str = ""
    library: str | None = None
    replaces: str | None = None
    overrides: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    packs: tuple[str, ...] = ()
    config_files: tuple[str, ...] = ()


def _strings(raw: object, where: str, pattern: re.Pattern[str] | None = None) -> tuple[str, ...]:
    if not isinstance(raw, list) or not all(isinstance(item, str) and item for item in raw):
        raise PackError(f"pack.toml {where} must be a list of strings")
    if pattern is not None:
        for item in raw:
            if not pattern.match(item):
                raise PackError(f"pack.toml {where}: {item!r} is not a valid name")
    return tuple(raw)


def load_manifest(pack_dir: Path) -> PackManifest:
    path = pack_dir / MANIFEST_FILE
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise PackError(f"{pack_dir} is not a pack: it has no {MANIFEST_FILE}") from None
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise PackError(f"{path} is unreadable: {exc}") from None
    for table, value in raw.items():
        if table not in _KEYS or not isinstance(value, dict):
            raise PackError(f"pack.toml: unknown table [{table}]")
        unknown = sorted(set(value) - _KEYS[table])
        if unknown:
            raise PackError(f"pack.toml [{table}]: unknown key {unknown[0]!r}")
    pack = raw.get("pack", {})
    name = pack.get("name")
    if not isinstance(name, str) or not PACK_NAME.match(name):
        raise PackError("pack.toml [pack] name must match [a-z][a-z0-9_]*")
    summary = pack.get("summary", "")
    library = pack.get("library")
    replaces = pack.get("replaces")
    if not isinstance(summary, str):
        raise PackError("pack.toml [pack] summary must be a string")
    if library is not None and (not isinstance(library, str) or Path(library).is_absolute()):
        raise PackError("pack.toml [pack] library must be a path relative to the pack")
    if library is not None and ".." in Path(library).parts:
        raise PackError("pack.toml [pack] library must stay inside the pack")
    if replaces is not None and (not isinstance(replaces, str) or not PACK_NAME.match(replaces)):
        raise PackError("pack.toml [pack] replaces must name one pack")
    if replaces == name:
        raise PackError("pack.toml [pack] replaces names the pack itself")
    requires = raw.get("requires", {})
    config = raw.get("config", {})
    return PackManifest(
        name=name,
        summary=summary,
        library=library,
        replaces=replaces,
        overrides=_strings(pack.get("overrides", []), "[pack] overrides", PLUGIN_NAME),
        tools=_strings(requires.get("tools", []), "[requires] tools"),
        packs=_strings(requires.get("packs", []), "[requires] packs", PACK_NAME),
        config_files=_strings(config.get("files", []), "[config] files", ENV_NAME),
    )


def wrapper_names(plugins_dir: Path) -> dict[str, Path]:
    """The plugin names a pack's wrapper folder publishes, read from source (no import), as the
    registry discovers them: the `@trestle` function's name, else the file's stem."""
    from trestle.server.snapshots import discover_plugin_name

    names: dict[str, Path] = {}
    if not plugins_dir.is_dir():
        return names
    for path in sorted(plugins_dir.glob("*.py")):
        if path.name.startswith("_"):
            continue
        try:
            name = discover_plugin_name(path)
        except (OSError, SyntaxError, UnicodeDecodeError):
            name = None
        names.setdefault(name or path.stem, path)
    return names


@dataclass(frozen=True)
class Library:
    """The pack's Python distribution: its directory, distribution name and version."""

    path: Path
    dist: str | None
    version: str | None


def read_library(pack_dir: Path, manifest: PackManifest) -> Library | None:
    if manifest.library is None:
        return None
    path = (pack_dir / manifest.library).resolve()
    pyproject = path / "pyproject.toml"
    if not pyproject.is_file():
        raise PackError(f"pack {manifest.name}: library {manifest.library!r} has no pyproject.toml")
    try:
        project = tomllib.loads(pyproject.read_text(encoding="utf-8")).get("project", {})
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise PackError(f"pack {manifest.name}: {pyproject} is unreadable: {exc}") from None
    dist = project.get("name") if isinstance(project, dict) else None
    version = project.get("version") if isinstance(project, dict) else None
    return Library(
        path=path,
        dist=dist if isinstance(dist, str) else None,
        version=version if isinstance(version, str) else None,
    )
