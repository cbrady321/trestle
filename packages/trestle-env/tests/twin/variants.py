"""The RB-12 proof nodes' one call to the `tree_variants` test plugin (L.RB-12.1; MC-12, MC-19).

Shared by the WR-UNIT-5/6 HOST nodes and their twins, like `twin.harness` for the reference
plugin: the plugin (`fixtures/tree_variants.py`) is published into the MCP host's plugin directory,
the HOST node binds the real container adapter, the twin sets the `fake_binding` seam; the call and
the record reads are the same.
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from tests.proof import mcp_host, records, tolerances
from trestle.common import clock

from twin import harness

PLUGIN = Path(__file__).resolve().parents[1] / "fixtures" / "tree_variants.py"
PLUGIN_NAME = "tree_variants"
POSTGRES = "backend.postgres"
HELPER = "backend.helper"
UP = "up"
STOP = "stop"


@contextmanager
def variants_host(
    home: Path, environ: Mapping[str, str | None] | None = None
) -> Iterator[mcp_host.McpHost]:
    """The MCP host with `tree_variants` published; `environ` set for it and its processes."""
    changes: dict[str, str | None] = {"PYTHONPATH": harness.plugin_pythonpath(), **(environ or {})}
    saved = {name: os.environ.get(name) for name in changes}
    try:
        for name, value in changes.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        (home / "plugins").mkdir(parents=True, exist_ok=True)
        (home / "plugins" / PLUGIN.name).write_bytes(PLUGIN.read_bytes())
        with mcp_host.McpHost(home=home, timeout_s=harness.HOST_TIMEOUT_S) as host:
            yield host
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def run_terminal(host: mcp_host.McpHost, env: str, mode: str) -> dict[str, Any]:
    """THE one call for a variant; MC-12 counts it once."""
    sent = host.request_count()
    answer = host.call(
        "run",
        {
            "plugin": PLUGIN_NAME,
            "args": {"env": env, "mode": mode},
            "wait_ms": tolerances.HARNESS_WAIT_MS,
            "completion": "terminal",
        },
    )
    assert host.request_count() == sent + 1, "one request, counted once by the MCP host"
    assert isinstance(answer, dict), answer
    harness.note_passed(answer)
    return answer


def created(entries: list[dict[str, Any]]) -> dict[str, str]:
    """Each vertex's confirmed create: path -> the selector the port named."""
    return {
        e["path"]: str(e["identity"])
        for e in entries
        if e["class"] == "confirmation" and e["effect"] == UP and e.get("status") == "applied"
    }


def claims_precede_creates(entries: list[dict[str, Any]]) -> bool:
    """MC-10: every create's claim (`issue`) is written before its confirmation."""
    for path in created(entries):
        rows = [
            (i, e["class"])
            for i, e in enumerate(entries)
            if e.get("path") == path and e.get("effect") == UP
        ]
        issued = [i for i, c in rows if c == "issue"]
        confirmed = [i for i, c in rows if c == "confirmation"]
        if not issued or not confirmed or issued[0] > confirmed[0]:
            return False
    return True


def release_order(entries: list[dict[str, Any]]) -> list[str]:
    """The vertices whose created container was released, in lane order."""
    return [e["path"] for e in entries if e["class"] == "released" and e["effect"] == UP]


def released_after_root_end(entries: list[dict[str, Any]]) -> bool:
    """Every owned stop was issued at the root's release phase: after the root's `end` row."""
    ends = [i for i, e in enumerate(entries) if e["class"] == "end" and e.get("path") == ""]
    stops = [i for i, e in enumerate(entries) if e["class"] == "issue" and e.get("effect") == STOP]
    return len(ends) == 1 and bool(stops) and all(i > ends[0] for i in stops)


# ---- a stopped root (L.RB-12.2) ------------------------------------------------------------

RELEASE_EFFECTS = frozenset({STOP})  # the tree's only `is_release` effect
STOP_WAIT_MS = int((clock.stop_bound + tolerances.JOIN_WAIT_S * 3) * 1000)


def start(host: mcp_host.McpHost, env: str, mode: str) -> str:
    """Admit a variant run and return its id at once (the stop comes from outside)."""
    started = host.call(
        "run", {"plugin": PLUGIN_NAME, "args": {"env": env, "mode": mode}, "wait_ms": 0}
    )
    assert isinstance(started, dict) and "run_id" in started, started
    return str(started["run_id"])


def both_created(host: mcp_host.McpHost, run_id: str) -> bool:
    """Both containers are confirmed created and the helper is done: the step is holding."""
    found = sorted((host.home / "runs").glob(f"*/{run_id}"))
    if not found:
        return False
    entries = [row.entry for row in records.lane_rows(found[0]).rows]
    ended = {e.get("path") for e in entries if e["class"] == "end"}
    return set(created(entries)) == {POSTGRES, HELPER} and HELPER in ended


def cancel_and_await(host: mcp_host.McpHost, run_id: str) -> dict[str, Any]:
    """Cancel the root through the MCP `cancel` tool, then await its terminal view."""
    host.call("cancel", {"run_id": run_id})
    joined = host.call(
        "await_runs", {"run_ids": [run_id], "mode": "all", "timeout_ms": STOP_WAIT_MS}
    )
    views = joined["result"] if isinstance(joined, dict) and "result" in joined else joined
    (view,) = views
    assert isinstance(view, dict), view
    return view


def applied_past_stop(run_dir_: Path) -> list[str]:
    """B2-C15: exactly one stop row, and no APPLIED non-release confirmation at or past the lane
    length it recorded (a release is the loop giving back what it made). The violations, or a
    reason the record proves nothing."""
    stops = [r for r in records.ledger_rows(run_dir_).rows if r.get("kind") == "stop_row"]
    if len(stops) != 1:
        return [f"{len(stops)} stop rows, not exactly one"]
    length = stops[0].get("lane_committed_length")
    if length is None:
        return ["the stop row's lane_committed_length is None (unproven)"]
    lane = records.lane_rows(run_dir_)
    if not lane.rows or lane.problems or lane.torn:
        return ["the lane is empty or unreadable", *lane.problems]
    return [
        f"APPLIED {row.entry['effect']} at byte {row.offset} is past offset {length}"
        for row in lane.rows
        if row.cls == "confirmation"
        and row.entry["status"] == "applied"
        and row.offset >= length
        and row.entry["effect"] not in RELEASE_EFFECTS
    ]
