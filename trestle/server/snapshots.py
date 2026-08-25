"""Plugin snapshot materialization."""

from __future__ import annotations

import ast
import hashlib
import json
import shutil
from pathlib import Path

from trestle.common.fsutil import atomic_write, sha256_file
from trestle.common.ids import generate_snapshot_id
from trestle.common.types import PluginSnapshot


def discover_plugin_name_from_source(source: str) -> str | None:
    tree = ast.parse(source)
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        for dec in node.decorator_list:
            if isinstance(dec, ast.Name) and dec.id == "trestle":
                return node.name
            if (
                isinstance(dec, ast.Call)
                and isinstance(dec.func, ast.Name)
                and dec.func.id == "trestle"
            ):
                return node.name
    return None


def discover_plugin_name(source_path: Path) -> str | None:
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef):
            continue
        for dec in node.decorator_list:
            if isinstance(dec, ast.Name) and dec.id == "trestle":
                return node.name
            if (
                isinstance(dec, ast.Call)
                and isinstance(dec.func, ast.Name)
                and dec.func.id == "trestle"
            ):
                return node.name
    return None


def materialize_snapshot(
    source_path: Path,
    plugin_id: str,
    *,
    home: Path,
    version: str = "0.1.0",
    summary_budget: int = 4096,
    timeout_s: int = 300,
) -> PluginSnapshot:
    source_sha256 = sha256_file(source_path)
    snapshot_id = generate_snapshot_id(source_sha256)
    snap_dir = home / "snapshots" / snapshot_id
    snap_dir.mkdir(parents=True, exist_ok=True)
    dest = snap_dir / "plugin.py"
    if not dest.exists():
        shutil.copy2(source_path, dest)
    schema_sha256 = hashlib.sha256(b"{}").hexdigest()
    manifest = {
        "plugin": plugin_id,
        "version": version,
        "source_sha256": source_sha256,
        "schema_sha256": schema_sha256,
    }
    manifest_sha256 = hashlib.sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    atomic_write(snap_dir / "manifest.json", json.dumps(manifest, indent=2).encode("utf-8"))
    return PluginSnapshot(
        snapshot_id=snapshot_id,
        plugin=plugin_id,
        version=version,
        source_path=str(dest),
        source_sha256=source_sha256,
        schema_sha256=schema_sha256,
        manifest_sha256=manifest_sha256,
        summary_budget=summary_budget,
        timeout_s=timeout_s,
    )
