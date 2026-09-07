"""Plugin directory resolution (APL-08 / PluginPublicationConfig)."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

CATALOG_HINT_EMPTY = (
    "No plugins published. Write a @trestle-decorated .py file into a "
    "plugin_search_paths directory, call publish_plugin, then list_plugins again."
)

CATALOG_HINT_PACKS_MISSING = (
    "Pack plugins require trestle-packs. Install with: "
    'pip install -e "packages/trestle-packs[all]" or pip install -e ".[packs]"'
)


def expand_plugin_path(raw: str, home: Path) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = (home / path).resolve()
    else:
        path = path.resolve()
    return path


def _paths_from_config(home: Path) -> list[Path] | None:
    path = home / "config.toml"
    if not path.exists():
        return None
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    plugins = raw.get("plugins")
    if not isinstance(plugins, dict):
        return None
    paths_raw = plugins.get("paths")
    if not isinstance(paths_raw, list) or not paths_raw:
        return None
    return [expand_plugin_path(str(entry), home) for entry in paths_raw]


def resolve_plugin_dirs(home: Path, *, cli_dirs: list[Path] | None = None) -> list[Path]:
    """Resolve watched plugin directories (normative order in interface design)."""
    if cli_dirs:
        return [expand_plugin_path(str(path), home) for path in cli_dirs]
    from_config = _paths_from_config(home)
    if from_config is not None:
        return from_config
    env = os.environ.get("TRESTLE_PLUGIN_DIRS")
    if env:
        parts = [part.strip() for part in env.split(":") if part.strip()]
        if parts:
            return [expand_plugin_path(part, home) for part in parts]
    return [(home / "plugins").resolve()]


def log_plugin_warning(home: Path, message: str) -> None:
    home.mkdir(parents=True, exist_ok=True)
    line = f"plugin_paths: {message}\n"
    (home / "service.log").open("a", encoding="utf-8").write(line)
