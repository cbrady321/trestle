"""Change 2b through the MCP proof host alone: every row of the record's gap table that names a
view or a field has it (docs/proposals/2026-10-22-agent-surface-2a-2d-design.md, 2b). The home is
touched only to write the operator's config.toml before the server starts, and to hold the
admission lock from outside for the `home_busy` row."""

from __future__ import annotations

import json
import os
import shutil
import signal
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tests.core.spine import support
from tests.proof import mcp_host, tolerances
from trestle.common import clock
from trestle.server.home import ADMISSION_LOCK, locks_dir, try_lock

FIXTURES = Path(__file__).resolve().parent / "fixtures"
WAIT_MS = int(tolerances.JOIN_WAIT_S * 3 * 1000)
HOST_TIMEOUT_S = 120.0 + clock.finalization_margin + tolerances.JOIN_WAIT_S
STATUS_KEYS = {
    "version",
    "profile",
    "draining",
    "home_format",
    "servers",
    "limits",
    "slots",
    "runs",
    "environments",
    "environments_truncated",
    "plugins",
    "plugin_dirs_missing",
}


@contextmanager
def _host(
    tmp_path: Path, config: str | None = None, *, env_hold: bool = False
) -> Iterator[mcp_host.McpHost]:
    home = tmp_path / "host-home"
    (home / "plugins").mkdir(parents=True)
    if config is not None:
        (home / "config.toml").write_text(config, encoding="utf-8")
    if env_hold:
        shutil.copy(FIXTURES / "env_hold.py", home / "plugins" / "env_hold.py")
    with mcp_host.McpHost(home=home, timeout_s=HOST_TIMEOUT_S) as host:
        yield host


def _status(host: Any) -> dict[str, Any]:
    out = host.call("query", {"view": "status"})
    assert out["next_cursor"] is None and out["truncated"] is False
    assert len(json.dumps(out, separators=(",", ":")).encode("utf-8")) <= 4096
    (row,) = out["items"]
    return dict(row)


def _hold(host: Any, **extra: Any) -> dict[str, Any]:
    return dict(
        host.call("run", {"plugin": "env_hold", "args": {"env": "dev"}, "wait_ms": 0, **extra})
    )


def _cancel_and_wait(host: Any, *run_ids: str) -> list[dict[str, Any]]:
    for run_id in run_ids:
        host.call("cancel", {"run_id": run_id})
    return list(
        host.call("await_runs", {"run_ids": list(run_ids), "timeout_ms": WAIT_MS})["result"]
    )


def _future(iso: str) -> bool:
    return datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC) > datetime.now(tz=UTC)


def test_environment_busy_names_the_holder_and_after_holds(tmp_path: Path) -> None:
    with _host(tmp_path, env_hold=True) as a:
        b = a.peer()
        try:
            holder = _hold(a)["run_id"]
            refused = _hold(b, deadline_s=30)
            assert refused["code"] == "admission.environment_busy", refused
            assert refused["retryable"] is True and "run_id" not in refused
            assert refused["held_by"] == holder
            assert _future(refused["free_at"])
            assert type(refused["retry_after_s"]) is int and refused["retry_after_s"] >= 1
            assert len(refused["message"]) <= 200 and holder in refused["message"]
            held = _hold(b, deadline_s=30, after={"run": refused["held_by"], "when": "ended"})
            assert held["state"] == "held", held
            env = _status(b)["environments"]
            assert env == [
                {"env": '"dev"', "held_by": holder, "free_at": refused["free_at"], "runs": 1}
            ]
            _cancel_and_wait(b, held["run_id"], holder)
        finally:
            b.close()


def test_queue_full_retry_after(tmp_path: Path) -> None:
    with _host(tmp_path, "max_running_runs = 1\nqueue_depth = 0\n") as host:
        first = host.call("run", {"plugin": "slow", "args": {"seconds": 60.0}, "wait_ms": 0})
        refused = host.call("run", {"plugin": "slow", "args": {"seconds": 1.0}, "wait_ms": 0})
        assert refused["code"] == "admission.queue_full", refused
        assert refused["retry_after_s"] == 5
        assert "held_by" not in refused and "free_at" not in refused
        _cancel_and_wait(host, first["run_id"])


def test_home_busy_retry_after(tmp_path: Path) -> None:
    with _host(tmp_path) as host:
        fd = try_lock(locks_dir(host.home) / ADMISSION_LOCK)
        assert fd is not None
        try:
            refused = host.call("run", {"plugin": "echo", "args": {"message": "x"}})
        finally:
            os.close(fd)
        assert refused["code"] == "admission.home_busy", refused
        assert refused["retry_after_s"] == 2


def test_service_draining_retry_after_and_status(tmp_path: Path) -> None:
    with _host(tmp_path) as host:
        live = host.call("run", {"plugin": "slow", "args": {"seconds": 20.0}, "wait_ms": 0})
        host.proc.send_signal(signal.SIGTERM)
        refused: dict[str, Any] = {}

        def draining() -> bool:
            refused.update(host.call("run", {"plugin": "echo", "args": {"message": "x"}}))
            return refused.get("code") == "admission.service_draining"

        assert support.wait_until(draining, tolerances.JOIN_WAIT_S), refused
        assert refused["retry_after_s"] == 10
        assert _status(host)["draining"] is True
        _cancel_and_wait(host, live["run_id"])


def test_allowlisted_only_under_the_restricted_profile(tmp_path: Path) -> None:
    restricted = '[profile]\nmode = "restricted"\nallowlist = ["echo"]\n'
    with _host(tmp_path / "r", restricted) as host:
        items = {item["name"]: item for item in host.call("list_plugins")["items"]}
        assert items["echo"]["allowlisted"] is True
        assert items["slow"]["allowlisted"] is False
        assert _status(host)["profile"] == "restricted"
    with _host(tmp_path / "f") as host:
        items = host.call("list_plugins")["items"]
        assert items and all("allowlisted" not in item for item in items)


def test_status_carries_the_ceilings(tmp_path: Path) -> None:
    config = "[operator]\ndeadline_ceiling_s = 1800\n\n[keys]\nmax_ttl_s = 3600\n"
    with _host(tmp_path, config) as host:
        limits = _status(host)["limits"]
        assert limits["deadline_ceiling_s"] == 1800
        assert limits["max_ttl_s"] == 3600


def test_status_row(tmp_path: Path) -> None:
    with _host(tmp_path) as host:
        idle = _status(host)
        assert set(idle) == STATUS_KEYS
        assert idle["profile"] == "full" and idle["draining"] is False
        assert idle["home_format"] == 2 and idle["servers"] == 1
        assert isinstance(idle["version"], str) and idle["version"]
        published = len(host.call("list_plugins")["items"])
        assert idle["plugins"] == {"published": published, "failed": 0}
        assert idle["plugin_dirs_missing"] == []
        assert set(idle["limits"]) == {
            "deadline_default_s",
            "deadline_ceiling_s",
            "finalization_margin_s",
            "idempotency_ttl_s",
            "max_ttl_s",
            "max_running_runs",
            "queue_depth",
            "max_held_runs",
        }
        assert idle["limits"]["deadline_default_s"] == 300
        live = host.call("run", {"plugin": "slow", "args": {"seconds": 60.0}, "wait_ms": 0})
        busy: dict[str, Any] = {}

        def running() -> bool:
            busy.update(_status(host))
            return bool(busy["slots"]["in_use"] >= 1 and busy["runs"]["running"] >= 1)

        assert support.wait_until(running, tolerances.JOIN_WAIT_S), busy
        assert busy["slots"]["free"] == busy["limits"]["max_running_runs"] - busy["slots"]["in_use"]
        assert busy["runs"]["session_ended"] == 0
        _cancel_and_wait(host, live["run_id"])


def test_an_orphan_holding_an_environment_can_be_ended(tmp_path: Path) -> None:
    restricted = '[profile]\nmode = "restricted"\nallowlist = ["env_hold"]\n'
    with _host(tmp_path, restricted, env_hold=True) as host:
        a = host.peer()
        b = host.peer()
        try:
            holder = _hold(a)["run_id"]
            refused = _hold(b, deadline_s=30)
            assert refused["held_by"] == holder
            params = {"run_id": refused["held_by"]}
            (row,) = b.call("query", {"view": "run", "params": params})["items"]
            assert row["session_ended"] is False
            a.close()  # DELETE: A's session ends, its run goes on
            (row,) = b.call("query", {"view": "run", "params": params})["items"]
            assert row["session_ended"] is True
            assert _status(b)["runs"]["session_ended"] == 1
            accepted = b.call("cancel", {"run_id": holder})
            assert accepted["code"] == "projection.cancel_accepted", accepted
            (view,) = b.call("await_runs", {"run_ids": [holder], "timeout_ms": WAIT_MS})["result"]
            assert view["state"] == "cancelled"
            again = _hold(b, deadline_s=30)
            assert "run_id" in again, again
            _cancel_and_wait(b, again["run_id"])
        finally:
            a.close()
            b.close()
