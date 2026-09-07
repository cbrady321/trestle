"""Plugin snapshot materialization."""

from __future__ import annotations

import ast
import hashlib
import json
import shutil
from pathlib import Path

from trestle.common.canonical import canonical_json
from trestle.common.fsutil import atomic_write, sha256_file
from trestle.common.ids import generate_snapshot_id
from trestle.common.types import PluginSnapshot
from trestle.server.plugin_schema import (
    find_trestle_function,
    schema_digest,
    schemas_from_source,
)
from trestle.server.plugin_validate import PluginValidationError, validate_plugin


def load_snapshot_schema(snap: PluginSnapshot) -> dict[str, object]:
    schema_path = Path(snap.source_path).with_name("schema.json")
    if schema_path.is_file():
        loaded = json.loads(schema_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            return loaded
    path = Path(snap.source_path)
    input_schema, _return_schema = schemas_from_source(
        path.read_text(encoding="utf-8"),
        source_path=path,
    )
    return input_schema


def load_snapshot_return_schema(snap: PluginSnapshot) -> dict[str, object]:
    schema_path = Path(snap.source_path).with_name("return_schema.json")
    if schema_path.is_file():
        loaded = json.loads(schema_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            return loaded
    path = Path(snap.source_path)
    _input_schema, return_schema = schemas_from_source(
        path.read_text(encoding="utf-8"),
        source_path=path,
    )
    return return_schema


def discover_plugin_name_from_source(source: str) -> str | None:
    fn = find_trestle_function(ast.parse(source))
    return None if fn is None else fn.name


def discover_plugin_name(source_path: Path) -> str | None:
    return discover_plugin_name_from_source(source_path.read_text(encoding="utf-8"))


def materialize_snapshot(
    source_path: Path,
    plugin_id: str,
    *,
    home: Path,
    version: str = "0.1.0",
    summary_budget: int = 4096,
    timeout_s: int = 300,
) -> PluginSnapshot:
    source = source_path.read_text(encoding="utf-8")
    schema, return_schema = schemas_from_source(source, source_path=source_path)
    error = validate_plugin(source_path)
    if error is not None:
        raise PluginValidationError(error)
    schema_bytes = canonical_json(schema)
    return_schema_bytes = canonical_json(return_schema)
    schema_sha256 = schema_digest(schema)
    source_sha256 = sha256_file(source_path)
    snapshot_id = generate_snapshot_id(source_sha256)
    snap_dir = home / "snapshots" / snapshot_id
    snap_dir.mkdir(parents=True, exist_ok=True)
    dest = snap_dir / "plugin.py"
    if not dest.exists():
        shutil.copy2(source_path, dest)
    atomic_write(snap_dir / "schema.json", schema_bytes)
    atomic_write(snap_dir / "return_schema.json", return_schema_bytes)
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
