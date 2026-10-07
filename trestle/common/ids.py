"""Opaque identifier generation."""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
import secrets
import time
from collections.abc import Mapping
from typing import Any

from trestle.common.canonical import canonical_json

_BASE32 = "0123456789abcdefghijklmnopqrstuv"


def _base32_encode(data: bytes) -> str:
    return base64.b32encode(data).decode("ascii").rstrip("=").lower()


def generate_run_id() -> str:
    """r_<base32-ms-timestamp><6 random base32> per R-RUN-2."""
    ts_ms = int(time.time() * 1000)
    ts_bytes = ts_ms.to_bytes(8, "big")
    rand = secrets.token_bytes(4)
    return f"r_{_base32_encode(ts_bytes)}{_base32_encode(rand)[:6]}"


def run_id_ms(run_id: str) -> int:
    """The millisecond timestamp a run id carries (`generate_run_id`: 13 base32 characters after
    `r_`). Run ids do not sort by time as strings (the base32 digits sort before its letters), so
    anything meaning "newest" orders on this; 0 when the id is not one."""
    try:
        raw = base64.b32decode(run_id[2:15].upper() + "===")
    except (binascii.Error, ValueError):
        return 0
    return int.from_bytes(raw[:8], "big")


def run_id_recency(run_id: str) -> tuple[int, str]:
    """Sort key for run ids (or run directory names): by the id's timestamp, then the id."""
    return (run_id_ms(run_id), run_id)


def generate_artifact_id() -> str:
    """art_<base32> per R-ART-6."""
    return f"art_{_base32_encode(secrets.token_bytes(12))}"


# The declared-tree slot of the identity (MC-18, MC-34). A plain plugin declares no tree, so
# the slot is empty; the tree phase fills it without changing the digest's shape.
DECLARED_TREE_SLOT_EMPTY = ""


def generate_snapshot_id(
    *,
    source_sha256: str,
    package_digests: Mapping[str, str],
    input_schema_sha256: str,
    return_schema_sha256: str,
    declared: Mapping[str, Any],
    summary_budget: int,
    runtime_version: str,
    declared_tree: str = DECLARED_TREE_SLOT_EMPTY,
) -> str:
    """`snap_` + 16 hex over everything that fixes what a run executes and what it declares:
    the plugin source, the digests of its declared packages, its input and return schema, its
    declared metadata (including the summary budget), the declared-tree slot and the runtime
    version (MC-18). Any one ingredient changing moves the id."""
    payload = {
        "source": source_sha256,
        "packages": dict(package_digests),
        "input_schema": input_schema_sha256,
        "return_schema": return_schema_sha256,
        "declared": dict(declared),
        "summary_budget": summary_budget,
        "declared_tree": declared_tree,
        "runtime": runtime_version,
    }
    return f"snap_{hashlib.sha256(canonical_json(payload)).hexdigest()[:16]}"


def generate_server_id() -> str:
    """`srv_<base32>`: one server process's identity (v0.3.1), recorded as `created.owner` and named
    by its lock file `home/servers/<server_id>.lock`. It replaces the service epoch."""
    return f"srv_{_base32_encode(os.urandom(8))}"
