"""R-VER-10 query backend conformance checks."""

from __future__ import annotations

from typing import Any

from trestle.query.views import VIEW_NAMES, VIEW_ROW_FIELDS


def semantic_items(envelope: dict[str, Any]) -> list[dict[str, object]]:
    """Strip backend/as_of for cross-backend comparison (R-VER-10)."""
    raw = envelope.get("items", [])
    if not isinstance(raw, list):
        return []
    return [dict(item) for item in raw if isinstance(item, dict)]


def validate_envelope(view: str, envelope: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if view not in VIEW_NAMES:
        errors.append(f"unknown view: {view}")
    for field in ("items", "truncated", "backend", "as_of"):
        if field not in envelope:
            errors.append(f"missing envelope field: {field}")
    if "next_cursor" not in envelope:
        errors.append("missing envelope field: next_cursor")
    items = envelope.get("items")
    if not isinstance(items, list):
        errors.append("items must be a list")
        return errors
    if not isinstance(envelope.get("truncated"), bool):
        errors.append("truncated must be bool")
    if not isinstance(envelope.get("backend"), str) or not envelope.get("backend"):
        errors.append("backend must be a non-empty string")
    if not isinstance(envelope.get("as_of"), str) or not envelope.get("as_of"):
        errors.append("as_of must be a non-empty string")
    next_cursor = envelope.get("next_cursor")
    if next_cursor is not None and not isinstance(next_cursor, str):
        errors.append("next_cursor must be string or null")
    for idx, row in enumerate(items):
        if not isinstance(row, dict):
            errors.append(f"row {idx} is not an object")
            continue
        errors.extend(validate_row(view, row, prefix=f"row {idx}"))
    return errors


def validate_row(view: str, row: dict[str, object], *, prefix: str = "row") -> list[str]:
    errors: list[str] = []
    expected = VIEW_ROW_FIELDS.get(view)
    if expected is None:
        return [f"unknown view: {view}"]
    keys = set(row.keys())
    missing = expected - keys
    extra = keys - expected
    if missing:
        errors.append(f"{prefix} missing fields: {sorted(missing)}")
    if extra:
        errors.append(f"{prefix} unexpected fields: {sorted(extra)}")
    return errors


def payloads_match(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return semantic_items(left) == semantic_items(right)
