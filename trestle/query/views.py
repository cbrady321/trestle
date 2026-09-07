"""View row builders and pagination helpers."""

from __future__ import annotations

import base64
import json
from typing import Any, Literal, get_args

ViewName = Literal[
    "run",
    "last_error",
    "run_tail",
    "run_events",
    "recent_runs",
    "recent_failures",
    "run_provenance",
    "run_artifacts",
    "artifact_refs",
]

VIEW_NAME_VALUES: tuple[str, ...] = get_args(ViewName)
VIEW_NAMES: frozenset[str] = frozenset(VIEW_NAME_VALUES)

FetchWindowKind = Literal["range", "head", "tail", "grep", "jsonpath"]
FETCH_WINDOW_KINDS: tuple[str, ...] = get_args(FetchWindowKind)

VIEW_ROW_FIELDS: dict[str, frozenset[str]] = {
    "run": frozenset({"run_id", "plugin", "version", "state", "started_at", "ended_at"}),
    "last_error": frozenset({"run_id", "event_seq", "kind", "message", "at"}),
    "run_tail": frozenset({"run_id", "line_no", "text"}),
    "run_events": frozenset({"run_id", "event_seq", "kind", "payload"}),
    "recent_runs": frozenset({"run_id", "plugin", "state", "started_at"}),
    "recent_failures": frozenset({"run_id", "plugin", "state", "failed_at"}),
    "run_provenance": frozenset(
        {"run_id", "snapshot_id", "spec_hash", "args_hash", "source_sha256"}
    ),
    "run_artifacts": frozenset({"run_id", "artifact_id", "name", "retention_class", "state"}),
    "artifact_refs": frozenset({"artifact_id", "producer_run_id", "referrer_run_id"}),
}

RUN_SCOPED_VIEWS: frozenset[str] = frozenset(
    {
        "run",
        "last_error",
        "run_tail",
        "run_events",
        "run_provenance",
        "run_artifacts",
    }
)

FINALIZED_VIEWS: frozenset[str] = frozenset(
    {
        "last_error",
        "run_tail",
        "run_events",
        "run_provenance",
        "run_artifacts",
        "recent_failures",
    }
)

RECENCY_CACHE_SIZE = 500
DEFAULT_PAGE_ITEMS = 50
DEFAULT_PAGE_BYTES = 64 * 1024
CELL_TRUNCATION_BYTES = 512
BACKEND_NAME = "filesystem"
CONSOLE_TAIL_BYTES = 2 * 1024


def truncate_cell(value: object, *, limit: int = CELL_TRUNCATION_BYTES) -> object:
    if not isinstance(value, str):
        return value
    encoded = value.encode("utf-8")
    if len(encoded) <= limit:
        return value
    truncated = encoded[:limit].decode("utf-8", errors="ignore")
    return truncated + "…"


def truncate_row(row: dict[str, object]) -> dict[str, object]:
    out: dict[str, object] = {}
    for key, value in row.items():
        if isinstance(value, str):
            out[key] = truncate_cell(value)
        else:
            out[key] = value
    return out


def encode_cursor(view: str, offset: int, token: str) -> str:
    payload = {"view": view, "offset": offset, "token": token}
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return "q_" + base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(cursor: str) -> tuple[str, int, str] | None:
    if not cursor.startswith("q_"):
        return None
    padded = cursor[2:] + "=" * (-len(cursor[2:]) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, json.JSONDecodeError):
        return None
    view = data.get("view")
    offset = data.get("offset")
    token = data.get("token")
    if not isinstance(view, str) or not isinstance(offset, int) or not isinstance(token, str):
        return None
    return view, offset, token


def paginate_rows(
    rows: list[dict[str, object]],
    *,
    view: str,
    offset: int,
    token: str,
    page_items: int = DEFAULT_PAGE_ITEMS,
    page_bytes: int = DEFAULT_PAGE_BYTES,
) -> tuple[list[dict[str, object]], str | None, bool]:
    if offset >= len(rows):
        return [], None, False

    items: list[dict[str, object]] = []
    used_bytes = 0
    idx = offset
    truncated = False

    while idx < len(rows) and len(items) < page_items:
        row = truncate_row(rows[idx])
        row_bytes = len(json.dumps(row, separators=(",", ":")).encode("utf-8"))
        if items and used_bytes + row_bytes > page_bytes:
            truncated = True
            break
        items.append(row)
        used_bytes += row_bytes
        idx += 1

    if idx < len(rows):
        truncated = True
        next_cursor = encode_cursor(view, idx, token)
        return items, next_cursor, truncated
    return items, None, False


def bounded_envelope(
    *,
    view: str,
    items: list[dict[str, object]],
    next_cursor: str | None,
    truncated: bool,
    as_of: str,
) -> dict[str, Any]:
    return {
        "items": items,
        "next_cursor": next_cursor,
        "truncated": truncated,
        "backend": BACKEND_NAME,
        "as_of": as_of,
    }
