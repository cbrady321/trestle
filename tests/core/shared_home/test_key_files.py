"""v0.3.1 Problem B, step 3: key expiry survives restarts.

A key's expiry is fixed once at admission and recorded in the `created` row (`key_expires_at`);
`home/keys/<sha256(key)>.json` lists every run that used the key, newest first, and is looked up
and claimed in one admission-locked step. Nothing rebuilds the files at start (the rebuild is a
repair: `doctor --rebuild-keys`, `init --upgrade`), so an expired key stays expired; a key whose
run directory is gone is free; and an interrupted run of a `repeatable` plugin frees its key for a
fresh run that records `retry_of`.
"""

from __future__ import annotations

import json
import shutil
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from tests.proof import mcp_host
from trestle import cli
from trestle.common import codes
from trestle.common.types import RequestOutcome, RunView
from trestle.server import idempotency as keys
from trestle.server import pool as pools
from trestle.server.config import RetentionConfig, TrestleConfig
from trestle.server.gc import run_gc
from trestle.server.ledger import RunLedger, ledger_path, read_state
from trestle.server.main import create_kernel
from trestle.server.reaper import Reaper, reap_home
from trestle.server.recovery import find_run_dir

HERE = Path(__file__).resolve().parent
PLUGINS = HERE / "plugins"
FIXTURE_PLUGINS = HERE.parents[1] / "fixtures" / "plugins"
WAIT_S = 15.0
POLL_S = 0.05
NO_WAIT_MS = 0
RUN_WAIT_MS = 5000
DAY_S = 86400.0


@pytest.fixture
def fast_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every server started from here stops a run at once (grace 0, kill 1 s)."""
    monkeypatch.setenv("TRESTLE_CANCEL_GRACE_S", "0")
    monkeypatch.setenv("TRESTLE_CANCEL_KILL_S", "1")


def _wait(predicate: Callable[[], bool], bound_s: float = WAIT_S) -> bool:
    deadline = time.monotonic() + bound_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(POLL_S)
    return predicate()


def _home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    (home / "plugins").mkdir(parents=True)
    for path in [*PLUGINS.glob("*.py"), FIXTURE_PLUGINS / "echo.py"]:
        shutil.copy(path, home / "plugins" / path.name)
    return home


def _kernel(home: Path, *, skip_recovery: bool = True) -> Any:
    return create_kernel(home=home, plugin_dirs=[home / "plugins"], skip_recovery=skip_recovery)


def _run_dir(home: Path, run_id: str) -> Path:
    found = find_run_dir(home, run_id)
    assert found is not None, run_id
    return found


def _created(home: Path, run_id: str) -> dict[str, Any]:
    created = RunLedger.open(ledger_path(_run_dir(home, run_id))).last_kind("created")
    assert created is not None
    return created


def _send(kernel: Any, args: dict[str, Any], key: str, plugin: str = "echo") -> Any:
    """`run` through the control surface (the path the MCP tool wraps), keyed."""
    return kernel.control.run(plugin, args, idempotency_key=key, wait_ms=RUN_WAIT_MS)


def _started(home: Path, run_id: str) -> bool:
    return RunLedger.open(ledger_path(_run_dir(home, run_id))).has_kind("started")


# --- Sequencing, step 3 --------------------------------------------------------------------------


def test_resend_after_interrupted_starts_a_fresh_run_only_for_a_repeatable_plugin(
    tmp_path: Path, fast_stop: None
) -> None:
    home = _home(tmp_path)
    gates = tmp_path / "gates"
    gates.mkdir()
    again = {"gate": str(gates / "again")}
    once = {"gate": str(gates / "once")}
    with mcp_host.McpHost(home=home) as a:
        assert a.call("describe_plugin", {"plugin_id": "repeat_gated"})["repeatable"] is True
        assert a.call("describe_plugin", {"plugin_id": "gated"})["repeatable"] is False
        sent = {
            "again": a.call(
                "run",
                {
                    "plugin": "repeat_gated",
                    "args": again,
                    "idempotency_key": "gate:again",
                    "wait_ms": NO_WAIT_MS,
                },
            )["run_id"],
            "once": a.call(
                "run",
                {
                    "plugin": "gated",
                    "args": once,
                    "idempotency_key": "gate:once",
                    "wait_ms": NO_WAIT_MS,
                },
            )["run_id"],
        }
        for run_id in sent.values():
            assert _wait(lambda r=run_id: _started(home, r))
        a.kill_server()
    reap_home(home)  # the owner is dead: both runs end interrupted
    for run_id in sent.values():
        state = read_state(_run_dir(home, run_id))
        assert state is not None and state["state"] == "interrupted"
    assert _created(home, sent["again"])["repeatable"] is True
    assert keys.read_entries(home, "gate:again")[0].repeatable is True

    kernel = _kernel(home)
    try:
        # repeatable: the identical re-send claims the key for a fresh run, linked by retry_of
        fresh = kernel.control.run(
            "repeat_gated", again, idempotency_key="gate:again", wait_ms=NO_WAIT_MS
        )
        assert isinstance(fresh, RunView) and fresh.run_id != sent["again"]
        assert fresh.retry_of == sent["again"]
        assert _created(home, fresh.run_id)["retry_of"] == sent["again"]
        assert [e.run_id for e in keys.read_entries(home, "gate:again")] == [
            fresh.run_id,
            sent["again"],
        ]
        # ... and a second identical re-send joins the fresh run, which is live
        joined = kernel.control.run(
            "repeat_gated", again, idempotency_key="gate:again", wait_ms=NO_WAIT_MS
        )
        assert isinstance(joined, RunView) and joined.run_id == fresh.run_id
        # not repeatable: the re-send returns the interrupted answer
        same = kernel.control.run("gated", once, idempotency_key="gate:once", wait_ms=NO_WAIT_MS)
        assert isinstance(same, RunView)
        assert (same.run_id, same.state) == (sent["once"], "interrupted")
        # other arguments are a conflict either way
        for plugin, key in (("repeat_gated", "gate:again"), ("gated", "gate:once")):
            other = _send(kernel, {"gate": str(gates / "other")}, key, plugin=plugin)
            assert isinstance(other, RequestOutcome)
            assert other.code == codes.IDEMPOTENCY_KEY_CONFLICT
    finally:
        for name in ("again", "once", "other"):
            (gates / name).write_text("open")
    view = kernel.control.project.await_terminal(fresh.run_id)
    assert isinstance(view, RunView) and view.state == "succeeded"
    assert view.retry_of == sent["again"]


# --- expiry is recorded, never revived ------------------------------------------------------------


def test_key_expiry_is_recorded_and_a_restart_does_not_revive_an_expired_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    home = _home(tmp_path)
    kernel = _kernel(home)
    first = _send(kernel, {"message": "a"}, "k:expired")
    assert isinstance(first, RunView)
    assert kernel.control.project.await_terminal(first.run_id).state == "succeeded"
    created = _created(home, first.run_id)
    for field in ("key_expires_at", "deadline_s", "idempotency_ttl_s", "finalization_margin_s"):
        assert field in created, field
    (entry,) = keys.read_entries(home, "k:expired")
    assert entry.key_expires_at == created["key_expires_at"]
    assert (entry.plugin, entry.call_deadline_s, entry.after) == ("echo", None, None)

    # three days later: the key's window is over
    later = time.time() + 3 * DAY_S
    monkeypatch.setattr(keys, "now", lambda: later)
    restarted = _kernel(home, skip_recovery=False)  # a start reaps, and rebuilds nothing
    assert keys.read_entries(home, "k:expired") == [entry]
    second = _send(restarted, {"message": "a"}, "k:expired")
    assert isinstance(second, RunView) and second.run_id != first.run_id
    # the repair replays the recorded expiry: the first run's key stays expired
    assert keys.rebuild_keys(home, ttl_s=3600) == 0
    assert cli.main(["doctor", "--rebuild-keys", "--home", str(home)]) == 0
    assert "keys rebuilt: 0" in capsys.readouterr().out
    entries = keys.read_entries(home, "k:expired")
    assert [e.run_id for e in entries] == [second.run_id, first.run_id]
    assert entries[1].key_expires_at == created["key_expires_at"] < later


def test_legacy_rebuild_takes_the_recorded_v030_expiry(tmp_path: Path) -> None:
    """A v0.3.0 created row (no key_expires_at) takes the expiry idempotency.json recorded; one
    that expired stays expired."""
    home = _home(tmp_path)
    kernel = _kernel(home)
    done = _send(kernel, {"message": "old"}, "k:legacy")
    assert kernel.control.project.await_terminal(done.run_id).state == "succeeded"
    ledger = ledger_path(_run_dir(home, done.run_id))
    rows = [json.loads(line) for line in ledger.read_text().splitlines()]
    for field in ("key_expires_at", "deadline_s", "idempotency_ttl_s", "finalization_margin_s"):
        rows[0].pop(field)
    ledger.write_text("".join(json.dumps(row) + "\n" for row in rows))
    past = time.time() - DAY_S
    (home / "idempotency.json").write_text(
        json.dumps({"entries": {"k:legacy": {"run_id": done.run_id, "expires_at": past}}})
    )
    shutil.rmtree(home / "keys")
    assert keys.rebuild_keys(home, ttl_s=3600, legacy=True) == 1
    (entry,) = keys.read_entries(home, "k:legacy")
    assert entry.key_expires_at == past
    assert keys.lookup(home, "k:legacy") is None
    # without a recorded expiry: v0.3.0's own formula, created.at + ttl + ceil(300 + margin)
    (home / "idempotency.json").unlink()
    assert keys.rebuild_keys(home, ttl_s=3600, legacy=True) == 1
    (entry,) = keys.read_entries(home, "k:legacy")
    at = pools.epoch(rows[0]["at"])
    assert entry.key_expires_at == keys.legacy_expires_at(at, 3600)


# --- a missing run is free -----------------------------------------------------------------------


def test_a_key_whose_run_is_gone_is_free_and_gc_removes_its_entry(tmp_path: Path) -> None:
    home = _home(tmp_path)
    kernel = _kernel(home)
    first = _send(kernel, {"message": "a"}, "k:gone")
    assert kernel.control.project.await_terminal(first.run_id).state == "succeeded"
    # the run directory is gone (a crash before the commit rename, or a removal): free, so even
    # other arguments claim the key instead of a conflict
    shutil.rmtree(_run_dir(home, first.run_id))
    second = _send(kernel, {"message": "b"}, "k:gone")
    assert isinstance(second, RunView) and second.run_id != first.run_id
    assert [e.run_id for e in keys.read_entries(home, "k:gone")] == [second.run_id]
    assert kernel.control.project.await_terminal(second.run_id).state == "succeeded"
    # GC removes a run's entry with the run
    keep_nothing = TrestleConfig(
        retention=RetentionConfig(metadata_days=0, artifact_days=0, abandoned_hours=0)
    )
    assert run_gc(home, keep_nothing).runs_removed == 1
    assert keys.read_entries(home, "k:gone") == []
    assert not keys.key_path(home, "k:gone").exists()


def test_reaper_purges_expired_entries_whose_run_is_gone(tmp_path: Path) -> None:
    home = _home(tmp_path)
    kernel = _kernel(home)
    kept = _send(kernel, {"message": "kept"}, "k:purge")
    assert kernel.control.project.await_terminal(kept.run_id).state == "succeeded"
    (live,) = keys.read_entries(home, "k:purge")
    stale = keys.KeyEntry(run_id="r_gone", plugin="echo", args_hash="x", key_expires_at=1.0)
    old = keys.KeyEntry(**{**live.to_dict(), "key_expires_at": 1.0})
    keys.write_entries(home, "k:purge", [stale, old])
    Reaper(home, server_id="test").pass_once()
    # the expired entry of a run that still exists stays, as history; the debris goes
    assert keys.read_entries(home, "k:purge") == [old]


def test_lookup_and_join_never_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = _home(tmp_path)
    kernel = _kernel(home)
    first = _send(kernel, {"message": "a"}, "k:read")
    assert kernel.control.project.await_terminal(first.run_id).state == "succeeded"
    path = keys.key_path(home, "k:read")
    before = path.stat().st_mtime_ns, path.read_bytes()
    joined = _send(kernel, {"message": "a"}, "k:read")
    assert isinstance(joined, RunView) and joined.run_id == first.run_id
    assert keys.lookup(home, "k:read") is not None
    assert (path.stat().st_mtime_ns, path.read_bytes()) == before
