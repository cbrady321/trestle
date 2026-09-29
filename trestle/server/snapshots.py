"""Plugin snapshot materialization."""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import json
import shutil
from pathlib import Path

import trestle
from trestle.common import codes
from trestle.common.canonical import canonical_json
from trestle.common.fsutil import atomic_write, sha256_file
from trestle.common.ids import DECLARED_TREE_SLOT_EMPTY, generate_snapshot_id
from trestle.common.plan.declared import DeclaredTree
from trestle.common.types import DeclaredMetadata, PluginSnapshot
from trestle.server.plugin_schema import (
    EntryError,
    declared_from_source,
    find_trestle_function,
    schema_digest,
    schemas_from_source,
)
from trestle.server.plugin_validate import (
    DeclarationInvalid,
    PluginValidationError,
    PublicationRefused,
    validate_and_extract,
)


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


def load_declared(snap: PluginSnapshot) -> DeclaredMetadata:
    """The declared metadata and entry name of a published snapshot (MC-18).

    The only reader of `manifest.json`'s `declared` and `entry`. A snapshot whose manifest
    predates them yields the defaults."""
    manifest_path = Path(snap.source_path).with_name("manifest.json")
    if manifest_path.is_file():
        loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            return DeclaredMetadata.from_manifest(loaded)
    return DeclaredMetadata()


DECLARATION_FILE = "declaration.json"


def load_declared_tree(snap: PluginSnapshot) -> DeclaredTree | None:
    """The declared tree of a published workflow snapshot (MC-34): `declaration.json` beside the
    snapshot's source. None for a plain plugin, which declares no tree. The only reader."""
    path = Path(snap.source_path).with_name(DECLARATION_FILE)
    if not path.is_file():
        return None
    return DeclaredTree.from_json(path.read_text(encoding="utf-8"))


DEADLINE_DECLARED = "declared"
DEADLINE_DEFAULT = "default"


def deadline_of(snap: PluginSnapshot) -> tuple[float, str]:
    """The deadline, in seconds, a call to this snapshot is admitted with, and where it comes from:
    the plugin's declared deadline (MC-18) or, for a plugin that declares none, the snapshot's
    default (300 s, WR-COMPAT-10)."""
    declared = load_declared(snap).deadline_s
    if declared is not None:
        return float(declared), DEADLINE_DECLARED
    return float(snap.timeout_s), DEADLINE_DEFAULT


def discover_plugin_name_from_source(source: str) -> str | None:
    try:
        fn = find_trestle_function(ast.parse(source))
    except EntryError:
        # No single entry: the source cannot be published under any name (schemas_from_source
        # names the reason), so there is no name to discover.
        return None
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
    declared = declared_from_source(source)
    outcome = validate_and_extract(
        source_path, entry=declared.entry, packages=declared.packages, env_arg=declared.env_arg
    )
    if outcome.error is not None:
        if outcome.code == codes.PUBLICATION_DECLARATION_INVALID:
            raise DeclarationInvalid(outcome.error)
        if outcome.code is not None:
            raise PublicationRefused(outcome.error, outcome.code)
        raise PluginValidationError(outcome.error)
    package_digests = outcome.package_digests
    tree = outcome.declaration
    declared = dataclasses.replace(declared, package_digests=package_digests)
    schema_bytes = canonical_json(schema)
    return_schema_bytes = canonical_json(return_schema)
    schema_sha256 = schema_digest(schema)
    source_sha256 = sha256_file(source_path)
    identity_declared = declared.declared_dict()
    del identity_declared["package_digests"]  # the digests enter the identity as their own slot
    snapshot_id = generate_snapshot_id(
        source_sha256=source_sha256,
        package_digests=package_digests,
        input_schema_sha256=schema_sha256,
        return_schema_sha256=schema_digest(return_schema),
        declared=identity_declared,
        summary_budget=summary_budget,
        runtime_version=trestle.__version__,
        declared_tree=DECLARED_TREE_SLOT_EMPTY if tree is None else tree.digest,
    )
    snap_dir = home / "snapshots" / snapshot_id
    snap_dir.mkdir(parents=True, exist_ok=True)
    dest = snap_dir / "plugin.py"
    if not dest.exists():
        shutil.copy2(source_path, dest)
    # A schema is never rewritten under an existing id: the id already covers both schemas.
    for name, content in (
        ("schema.json", schema_bytes),
        ("return_schema.json", return_schema_bytes),
    ):
        if not (snap_dir / name).exists():
            atomic_write(snap_dir / name, content)
    if tree is not None and not (snap_dir / DECLARATION_FILE).exists():
        atomic_write(snap_dir / DECLARATION_FILE, tree.to_json().encode("utf-8"))
    manifest = {
        "plugin": plugin_id,
        "version": version,
        "source_sha256": source_sha256,
        "schema_sha256": schema_sha256,
        "declared": declared.declared_dict(),
        "entry": declared.entry,
    }
    if tree is not None:
        manifest["declaration_digest"] = tree.digest
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
