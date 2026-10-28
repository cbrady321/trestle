"""Change 2b: `query(view="status")`, one row saying what this service allows and how busy it is.

Answered under both profiles, to any session, while draining too: nothing in it is session scoped.
It never shows anything about credentials (identities, grants, the store), server ids, pids or
ports, the admission lock's holder, storage, the allowlist itself, or session ids. The whole
serialized envelope stays within `STATUS_BUDGET_BYTES` (every list is capped, every long string
cut).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import trestle
from trestle.query import views as view_defs
from trestle.query.conformance import validate_envelope
from trestle.server import pool as pools
from trestle.server.config import load_config
from trestle.server.home import (
    home_format,
    live_run_ids,
    live_servers,
    marked_run_dir,
    read_marker,
)
from trestle.server.ledger import read_state
from trestle.server.mcp_sessions import SessionReader

if TYPE_CHECKING:
    from trestle.server.control import ControlSurface

STATUS_BUDGET_BYTES = 4096
ENVIRONMENTS_MAX = 8
ENV_MAX_BYTES = 96
PLUGIN_DIRS_MAX = 4
PLUGIN_DIR_MAX_BYTES = 200
# the deadline of a plugin that declares none (`materialize_snapshot`'s `timeout_s`)
DEFAULT_DEADLINE_S = 300
_LIVE_STATES = ("queued", "held", "running")

STATUS_FIELDS: frozenset[str] = view_defs.VIEW_ROW_FIELDS["status"]


def cut_bytes(text: str, limit: int) -> str:
    """`text` within `limit` UTF-8 bytes, ending in `…` when it was cut (on a character
    boundary)."""
    raw = text.encode("utf-8")
    if len(raw) <= limit:
        return text
    mark = "…".encode()
    return raw[: limit - len(mark)].decode("utf-8", errors="ignore") + "…"


def _iso(epoch: float) -> str:
    from datetime import UTC, datetime

    return datetime.fromtimestamp(epoch, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def environments(state: pools.State | None) -> tuple[list[dict[str, Any]], bool]:
    """The environments in use, home wide: each key's `held_by` and `free_at` (exactly what an
    `admission.environment_busy` refusal would say) and its running plus queued count, soonest
    `free_at` first, at most `ENVIRONMENTS_MAX`."""
    if state is None:
        return [], False
    counts: dict[str, int] = {}
    for _, key in pools.key_runs(state):
        counts[key] = counts.get(key, 0) + 1
    rows: list[tuple[float, str, dict[str, Any]]] = []
    for key, count in counts.items():
        holder = pools.busy_holder(state, key)
        if holder is None:
            continue
        held_by, free_at = holder
        rows.append(
            (
                free_at,
                key,
                {
                    "env": cut_bytes(key, ENV_MAX_BYTES),
                    "held_by": held_by,
                    "free_at": _iso(free_at),
                    "runs": count,
                },
            )
        )
    rows.sort(key=lambda row: (row[0], row[1]))
    return [row[2] for row in rows[:ENVIRONMENTS_MAX]], len(rows) > ENVIRONMENTS_MAX


def live_runs(control: ControlSurface) -> dict[str, int]:
    """The live runs by state, home wide (the live markers), and how many of them arrived on an
    MCP session that has ended."""
    home = control.project.home
    counts = {name: 0 for name in _LIVE_STATES}
    ended = 0
    reader = SessionReader(home)
    for run_id in live_run_ids(home):
        marker = read_marker(home, run_id)
        if marker is None:
            continue
        state = marker.get("state")
        if state in counts:
            counts[str(state)] += 1
        run_dir = marked_run_dir(home, run_id, marker)
        record = read_state(run_dir) if run_dir is not None else None
        if record is not None and reader.ended(record, run_dir):
            ended += 1
    return {**counts, "session_ended": ended}


def build_status(control: ControlSurface) -> dict[str, Any]:
    """The status row (fields: `STATUS_FIELDS`)."""
    home = control.project.home
    config = load_config(home)
    scheduler = control.scheduler
    sched = pools.read_sched(home)
    in_use = len(sched["running"]) if sched is not None else 0
    envs, envs_truncated = environments(sched)
    registry = control.project.registry
    missing = [path for path in registry.plugin_dirs if not path.is_dir()]
    return {
        "version": control.version or trestle.__version__,
        "profile": control.admission.profile.mode,
        "draining": scheduler.draining,
        "home_format": home_format(home),
        "servers": len(live_servers(home)),
        "limits": {
            "deadline_default_s": DEFAULT_DEADLINE_S,
            "deadline_ceiling_s": config.operator_limits.deadline_ceiling,
            "finalization_margin_s": config.operator_limits.finalization_margin,
            "idempotency_ttl_s": config.idempotency_ttl_s,
            "max_ttl_s": config.max_ttl_s,
            "max_running_runs": config.max_running_runs,
            "queue_depth": scheduler.queue_depth,
            "max_held_runs": scheduler.max_held,
        },
        "slots": {"in_use": in_use, "free": max(0, config.max_running_runs - in_use)},
        "runs": live_runs(control),
        "environments": envs,
        "environments_truncated": envs_truncated,
        "plugins": {"published": len(registry.snapshots), "failed": registry.failure_count()},
        "plugin_dirs_missing": [
            cut_bytes(str(path), PLUGIN_DIR_MAX_BYTES) for path in missing[:PLUGIN_DIRS_MAX]
        ],
    }


def status_envelope(control: ControlSurface) -> dict[str, Any]:
    """The `status` view's envelope: one row, no cursor, never truncated; params are ignored."""
    envelope = view_defs.bounded_envelope(
        view="status",
        items=[build_status(control)],
        next_cursor=None,
        truncated=False,
        as_of=_now_iso(),
    )
    errors = validate_envelope("status", envelope)
    if errors:
        raise RuntimeError(f"query conformance failed for status: {errors[0]}")
    return envelope


def _now_iso() -> str:
    import time

    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
