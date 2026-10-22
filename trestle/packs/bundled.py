"""Bundled packs: `packages/trestle-pack-<name>/` of the running release's checkout (P-2). They
install by name, with owner `trestle`, and nothing else can claim that owner (P-5)."""

from __future__ import annotations

import json
import os
from pathlib import Path

from trestle.packs.manifest import MANIFEST_FILE, PLUGINS_DIR, wrapper_names

BUNDLED_PREFIX = "trestle-pack-"
# where the release's `packages/` folder is, when this process does not run from the checkout
BUNDLED_ENV = "TRESTLE_BUNDLED_PACKS"


def bundled_root() -> Path | None:
    """The running release's `packages/` folder: `$TRESTLE_BUNDLED_PACKS`, else the checkout this
    package is imported from (editable), else the local folder pip installed it from. None when
    this build has no checkout to install bundled packs from (P-2: no wheels yet)."""
    env = os.environ.get(BUNDLED_ENV)
    if env:
        return Path(env).expanduser().resolve()
    from trestle.common.build import checkout_dir

    root = checkout_dir()
    if root is not None:
        return root / "packages"
    try:
        from importlib.metadata import distribution

        raw = distribution("trestle").read_text("direct_url.json")
        url = str(json.loads(raw).get("url", "")) if raw else ""
    except Exception:
        url = ""
    if url.startswith("file://"):
        packages = Path(url.removeprefix("file://")) / "packages"
        if packages.is_dir():
            return packages
    return None


def bundled_dir(name: str, root: Path | None = None) -> Path | None:
    base = root if root is not None else bundled_root()
    if base is None:
        return None
    path = base / f"{BUNDLED_PREFIX}{name}"
    return path if (path / MANIFEST_FILE).is_file() else None


def available_bundled(root: Path | None = None) -> dict[str, Path]:
    """Every bundled pack of this release, by name."""
    base = root if root is not None else bundled_root()
    if base is None or not base.is_dir():
        return {}
    out: dict[str, Path] = {}
    for path in sorted(base.glob(f"{BUNDLED_PREFIX}*")):
        if (path / MANIFEST_FILE).is_file():
            out[path.name.removeprefix(BUNDLED_PREFIX)] = path
    return out


def bundled_plugin_index(root: Path | None = None) -> dict[str, str]:
    """Plugin name -> the bundled pack that provides it, read from the wrapper sources (no
    import), for `admission.plugin_not_found`'s hint."""
    index: dict[str, str] = {}
    for pack, path in available_bundled(root).items():
        for plugin in wrapper_names(path / PLUGINS_DIR):
            index.setdefault(plugin, pack)
    return index
