"""Change 2a through the MCP proof host alone: a run outlives its session untouched and is marked
`session_ended`; a dropped connection is not a session end; a severed terminal wait leaves the run
to end on its own; under the restricted profile another session may cancel a run once its session
has ended, and under the full profile at once (docs/proposals/2026-10-22-agent-surface-2a-2d-
design.md, 2a). Every bound comes from `tests.proof.tolerances` or `trestle.common.clock`."""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from tests.core.spine import support
from tests.proof import mcp_host, tolerances
from trestle.common import clock

SLOW_S = 6.0
# a run's admission, start and end under a loaded host (the full suite runs 8-wide)
STARTUP_WAIT_S = tolerances.JOIN_WAIT_S * 3
HOST_TIMEOUT_S = 300.0 + clock.finalization_margin + tolerances.JOIN_WAIT_S


@contextmanager
def _hosts(tmp_path: Path, config: str | None = None) -> Iterator[tuple[Any, Any]]:
    home = tmp_path / "host-home"
    home.mkdir(parents=True)
    if config is not None:
        (home / "config.toml").write_text(config, encoding="utf-8")
    with mcp_host.McpHost(home=home, timeout_s=HOST_TIMEOUT_S) as a:
        b = a.peer()
        try:
            yield a, b
        finally:
            b.close()


def _row(host: Any, view: str, run_id: str) -> dict[str, Any]:
    params = {"run_id": run_id} if view == "run" else {}
    out = host.call("query", {"view": view, "params": params})
    (row,) = [item for item in out["items"] if item["run_id"] == run_id]
    return dict(row)


def _visible(host: Any) -> str:
    """The one run's id once the peer sees it (it was sent from the other session)."""
    found: list[str] = []

    def seen() -> bool:
        items = host.call("query", {"view": "recent_runs"})["items"]
        found[:] = [item["run_id"] for item in items]
        return bool(found)

    assert support.wait_until(seen, STARTUP_WAIT_S), "the run was never admitted"
    (run_id,) = found
    return run_id


def _await(host: Any, run_id: str) -> dict[str, Any]:
    out = host.call("await_runs", {"run_ids": [run_id], "timeout_ms": int(STARTUP_WAIT_S * 1000)})
    (view,) = out["result"]
    return dict(view)


def _passed_untouched(view: dict[str, Any]) -> None:
    assert view["state"] == "succeeded", view
    assert view["answer"]["outcome"] == "passed", view
    assert view["answer"]["root_stop"] is None, view
    assert view.get("error") is None, view


def test_deleted_session_leaves_the_run_and_marks_it(tmp_path: Path) -> None:
    with _hosts(tmp_path) as (a, b):
        frame = a.call("run", {"plugin": "slow", "args": {"seconds": SLOW_S + 2}, "wait_ms": 0})
        run_id = frame["run_id"]
        a.sever("session_close")
        recent = _row(b, "recent_runs", run_id)
        run = _row(b, "run", run_id)
        assert run["state"] in ("queued", "running"), run
        assert recent["session_ended"] is True, recent
        assert run["session_ended"] is True, run
        _passed_untouched(_await(b, run_id))
        assert _row(b, "run", run_id)["session_ended"] is None
        assert _row(b, "recent_runs", run_id)["session_ended"] is None


def test_connection_drop_is_not_a_session_end(tmp_path: Path) -> None:
    with _hosts(tmp_path) as (a, b):
        held = a.hold(
            "run",
            {
                "plugin": "slow",
                "args": {"seconds": SLOW_S},
                "wait_ms": 1000,
                "completion": "terminal",
            },
        )
        run_id = _visible(b)
        a.sever("connection_drop", req_id=held)
        assert a.join_outcome(held, timeout=tolerances.JOIN_WAIT_S).error is not None
        run = _row(b, "run", run_id)
        if run["state"] in ("queued", "running"):
            assert run["session_ended"] is False, run
        _passed_untouched(_await(b, run_id))


@pytest.mark.parametrize("mode", ["connection_drop", "session_close", "cancel_notification"])
def test_dropped_terminal_wait(tmp_path: Path, mode: str) -> None:
    key = f"dropped-wait-{mode}"
    request = {
        "plugin": "slow",
        "args": {"seconds": SLOW_S},
        "wait_ms": 1000,
        "completion": "terminal",
        "idempotency_key": key,
    }
    with _hosts(tmp_path) as (a, b):
        held = a.hold("run", request)
        run_id = _visible(b)
        if mode == "session_close":
            a.sever(mode)
        else:
            a.sever(mode, req_id=held)
        time.sleep(tolerances.SETTLE_LONG_S)
        view = _await(b, run_id)
        _passed_untouched(view)
        rows = b.call("query", {"view": "run_by_key", "params": {"idempotency_key": key}})
        (row,) = rows["items"]
        assert row["run_id"] == run_id and row["joinable"] is True, row
        again = b.call("run", request)
        assert again["run_id"] == run_id, again
        assert again["state"] == "succeeded", again
        items = b.call("query", {"view": "recent_runs"})["items"]
        assert [item["run_id"] for item in items] == [run_id]


RESTRICTED = '[profile]\nmode = "restricted"\nallowlist = ["slow"]\n'


def test_restricted_cancel_follows_the_session(tmp_path: Path) -> None:
    with _hosts(tmp_path, RESTRICTED) as (a, b):
        frame = a.call("run", {"plugin": "slow", "args": {"seconds": 60.0}, "wait_ms": 0})
        run_id = frame["run_id"]
        refused = b.call("cancel", {"run_id": run_id})
        assert refused["code"] == "projection.not_owner", refused
        assert "its session has not ended" in refused["message"]
        a.sever("session_close")
        accepted = b.call("cancel", {"run_id": run_id})
        assert accepted["code"] == "projection.cancel_accepted", accepted
        assert _await(b, run_id)["state"] == "cancelled"


def test_full_profile_cancel_unchanged(tmp_path: Path) -> None:
    with _hosts(tmp_path) as (a, b):
        frame = a.call("run", {"plugin": "slow", "args": {"seconds": 60.0}, "wait_ms": 0})
        run_id = frame["run_id"]
        accepted = b.call("cancel", {"run_id": run_id})
        assert accepted["code"] == "projection.cancel_accepted", accepted
        assert _await(b, run_id)["state"] == "cancelled"
