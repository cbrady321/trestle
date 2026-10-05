"""Canonical JSON serialization for args hashing."""

from __future__ import annotations

import json
import math
from typing import Any

from trestle.common.fsutil import sha256_bytes


class NonCanonical(ValueError):
    pass


def canonical_json(value: Any) -> bytes:
    return json.dumps(_to_canonical(value), separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def args_hash(args: dict[str, Any]) -> str:
    return sha256_bytes(canonical_json(args))


def _to_canonical(value: Any) -> Any:
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            raise NonCanonical("non_canonical_value")
        return value
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, dict):
        return {k: _to_canonical(v) for k, v in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_to_canonical(v) for v in value]
    raise TypeError(type(value))
