"""A run's pack provenance, recorded by the host at admission (P-1, P-20): the pack identity its
snapshot was stamped with at publication, and the sha256 of each configuration file the pack
names in `pack.toml [config] files`. The host reads the files, not the plugin, so a plugin cannot
misreport them: two runs with the same snapshot and the same configuration digests ran the same
code with the same settings."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from trestle.common.fsutil import sha256_file
from trestle.common.types import PluginSnapshot


def load_pack_stamp(snap: PluginSnapshot) -> dict[str, Any] | None:
    """The `pack` object of the snapshot's manifest; None for a loose file or runtime publish."""
    try:
        manifest = json.loads(
            Path(snap.source_path).with_name("manifest.json").read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None
    stamp = manifest.get("pack") if isinstance(manifest, dict) else None
    return stamp if isinstance(stamp, dict) else None


def config_digests(home: Path, variables: list[str]) -> dict[str, dict[str, str | None]]:
    """Each variable's file as a run will see it: `[plugins.env]` over the service environment
    (the run's own environment, G1). `sha256` is None when the variable is unset or the file
    cannot be read; the run still starts, and the provenance says so."""
    from trestle.server.config import load_config

    env = dict(os.environ)
    env.update(load_config(home).plugin_env)
    out: dict[str, dict[str, str | None]] = {}
    for name in variables:
        raw = env.get(name)
        if not raw:
            out[name] = {"path": None, "sha256": None}
            continue
        path = Path(raw).expanduser()
        try:
            digest: str | None = sha256_file(path)
        except OSError:
            digest = None
        out[name] = {"path": str(path), "sha256": digest}
    return out


def pack_provenance(home: Path, snap: PluginSnapshot) -> dict[str, Any]:
    """The `pack` and `config` entries of a run's spec provenance (empty for a non-pack plugin)."""
    stamp = load_pack_stamp(snap)
    if stamp is None:
        return {}
    identity = {key: str(stamp[key]) for key in ("name", "version", "owner") if key in stamp}
    out: dict[str, Any] = {"pack": identity}
    files = stamp.get("config_files")
    if isinstance(files, list) and files:
        out["config"] = config_digests(home, [str(name) for name in files])
    return out
