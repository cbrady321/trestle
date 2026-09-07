"""Opaque identifier generation."""

from __future__ import annotations

import base64
import os
import secrets
import time

_BASE32 = "0123456789abcdefghijklmnopqrstuv"


def _base32_encode(data: bytes) -> str:
    return base64.b32encode(data).decode("ascii").rstrip("=").lower()


def generate_run_id() -> str:
    """r_<base32-ms-timestamp><6 random base32> per R-RUN-2."""
    ts_ms = int(time.time() * 1000)
    ts_bytes = ts_ms.to_bytes(8, "big")
    rand = secrets.token_bytes(4)
    return f"r_{_base32_encode(ts_bytes)}{_base32_encode(rand)[:6]}"


def generate_artifact_id() -> str:
    """art_<base32> per R-ART-6."""
    return f"art_{_base32_encode(secrets.token_bytes(12))}"


def generate_snapshot_id(source_sha256: str) -> str:
    return f"snap_{source_sha256[:16]}"


def generate_service_epoch() -> str:
    return f"epoch_{_base32_encode(os.urandom(8))}"
