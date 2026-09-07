"""R-MCP-9 view catalog — when to pick each named view (not a schema dump)."""

from __future__ import annotations

from typing import Any

from trestle.query.views import (
    CELL_TRUNCATION_BYTES,
    DEFAULT_PAGE_BYTES,
    DEFAULT_PAGE_ITEMS,
    FETCH_WINDOW_KINDS,
    VIEW_NAME_VALUES,
)

VIEW_CATALOG_URI = "trestle://views"

_PAGE_KB = DEFAULT_PAGE_BYTES // 1024

_VIEW_WHEN: dict[str, dict[str, str]] = {
    "run": {
        "use_when": "Status and timing for one run (plugin, state, started_at, ended_at).",
        "params": "run_id",
    },
    "last_error": {
        "use_when": "Why a run failed — last error event. Empty items if the run succeeded.",
        "params": "run_id",
    },
    "run_tail": {
        "use_when": (
            "Wrapper stdout/stderr lines from print. Empty when the plugin only used ctx.log "
            "(use run_events) or returned a value (fetch {run_id}/result). Not TTY-class "
            "console — that is fetch art_…."
        ),
        "params": "run_id",
    },
    "run_events": {
        "use_when": (
            f"Structured events from ctx.log. Pager of {DEFAULT_PAGE_ITEMS} rows / "
            f"{_PAGE_KB} KB with {CELL_TRUNCATION_BYTES}-byte cells — not event grep. "
            "For a pattern in result or artifact text, fetch with kind=grep."
        ),
        "params": "run_id",
    },
    "recent_runs": {
        "use_when": "Browse recent runs, newest first. Follow next_cursor when truncated.",
        "params": "",
    },
    "recent_failures": {
        "use_when": "Browse recent failed runs, newest first.",
        "params": "",
    },
    "run_provenance": {
        "use_when": "Snapshot id and hashes for one run.",
        "params": "run_id",
    },
    "run_artifacts": {
        "use_when": "Artifact ids for one run; then fetch the art_… handle.",
        "params": "run_id",
    },
    "artifact_refs": {
        "use_when": "Producer and referrer runs for one artifact_id.",
        "params": "artifact_id",
    },
}

_FETCH_TARGETS: dict[str, str] = {
    "{run_id}/result": ("Plugin return value. kinds: jsonpath, range, head, tail, grep."),
    "art_…": (
        "Artifact bytes (including TTY-class console). kinds: range, head, tail, grep. "
        "Not run_tail."
    ),
    "summary.handle": "Array continuation from a truncated RunView. kinds: jsonpath, range.",
}


def view_catalog() -> dict[str, Any]:
    """Read-only catalog of frozen views and fetch targets (R-MCP-9)."""
    missing = [name for name in VIEW_NAME_VALUES if name not in _VIEW_WHEN]
    if missing:
        raise RuntimeError(f"view catalog missing entries: {missing}")
    return {
        "views": [{"name": name, **_VIEW_WHEN[name]} for name in VIEW_NAME_VALUES],
        "fetch": {
            "use_when": "Bytes of one opaque handle — not a named view, not a filesystem path.",
            "targets": _FETCH_TARGETS,
            "kinds": list(FETCH_WINDOW_KINDS),
        },
        "mouths": {
            "print": "query view=run_tail",
            "ctx.log": "query view=run_events (pager, not grep)",
            "return_value": "fetch {run_id}/result",
            "artifact": "fetch art_…",
        },
        "invalid_view": (
            "Unknown names and SQL strings are projection.invalid_view. "
            "Do not invent views; do not send SQL on MCP."
        ),
    }
