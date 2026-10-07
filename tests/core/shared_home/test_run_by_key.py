"""v0.4 step 5: `run_by_key`, `await_runs(keys=[...])` and `run(idempotency_ttl_s=...)`.

`query(view="run_by_key")` answers from the key's file in one read (its own path, never the
recency window, no admission lock), one row per run that used the key, newest first.
`await_runs` takes keys beside run ids: each key resolves to its newest run before the wait, the
answer lists run ids then keys, and only a view reached through a key carries `idempotency_key`.
`idempotency_ttl_s` is bounded by `[keys] max_ttl_s` and fixed into `key_expires_at` at admission.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from tests.proof import mcp_host
from trestle.common import clock, codes
from trestle.common.types import RequestOutcome, RunView
from trestle.query import views as view_defs
from trestle.query.catalog import view_catalog
from trestle.query.fs import FilesystemQueryBackend
from trestle.server import idempotency as keys
from trestle.server.config import load_config
from trestle.server.home import admission_lock
from trestle.server.ledger import RunLedger, ledger_path
from trestle.server.main import create_kernel
from trestle.server.recovery import find_run_dir

HERE = Path(__file__).resolve().parent
FIXTURES = HERE.parents[1] / "fixtures" / "plugins"
RUN_WAIT_MS = 5000
HOUR_S = 3600.0
DAY_S = 86400.0
JOIN_S = 1.0


def _home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    (home / "plugins").mkdir(parents=True)
    for path in [
        HERE / "plugins" / "boom.py",
        FIXTURES / "echo.py",
        FIXTURES / "outputs_writer.py",
    ]:
        shutil.copy(path, home / "plugins" / path.name)
    return home


def _kernel(home: Path) -> Any:
    return create_kernel(home=home, plugin_dirs=[home / "plugins"], skip_recovery=True)


def _send(
    kernel: Any, key: str, plugin: str = "echo", args: dict[str, Any] | None = None, **kw: Any
) -> RunView:
    view = kernel.control.run(
        plugin,
        args if args is not None else {"message": "a"},
        idempotency_key=key,
        wait_ms=RUN_WAIT_MS,
        completion="terminal",
        **kw,
    )
    assert isinstance(view, RunView), view
    return view


def _rows(kernel: Any, key: str) -> dict[str, Any]:
    out = kernel.control.query("run_by_key", {"idempotency_key": key})
    assert isinstance(out, dict), out
    return out


def _created(home: Path, run_id: str) -> dict[str, Any]:
    run_dir = find_run_dir(home, run_id)
    assert run_dir is not None
    created = RunLedger.open(ledger_path(run_dir)).last_kind("created")
    assert created is not None
    return created


# --- run_by_key ----------------------------------------------------------------------------------


def test_run_by_key_is_a_registered_view_with_the_designed_row_fields() -> None:
    assert "run_by_key" in view_defs.VIEW_NAMES
    assert view_defs.VIEW_ROW_FIELDS["run_by_key"] == frozenset(
        {
            "idempotency_key", "run_id", "plugin", "retry_of", "state", "started_at", "ended_at",
            "deadline_s", "outcome_class", "error_code", "joinable", "key_expires_at", "summary",
            "summary_truncated", "artifact_count", "artifacts_available",
        }
    )  # fmt: skip
    entry = next(v for v in view_catalog()["views"] if v["name"] == "run_by_key")
    assert entry["params"] == "idempotency_key" and "key" in entry["use_when"]


def test_run_by_key_rows_newest_first_with_outcome_summary_and_joinable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _home(tmp_path)
    kernel = _kernel(home)
    first = _send(kernel, "gate:1", "outputs_writer", {"name": "log.txt", "body": "x"})
    created = _created(home, first.run_id)
    page = _rows(kernel, "gate:1")
    assert page["truncated"] is False and page["next_cursor"] is None
    (row,) = page["items"]
    assert set(row) == view_defs.VIEW_ROW_FIELDS["run_by_key"]
    assert (row["idempotency_key"], row["run_id"], row["plugin"]) == (
        "gate:1", first.run_id, "outputs_writer",
    )  # fmt: skip
    assert (row["state"], row["outcome_class"], row["error_code"]) == ("succeeded", "passed", None)
    assert row["outcome_class"] == first.outcome["class"]
    assert row["retry_of"] is None and row["deadline_s"] == 300
    assert row["started_at"] and row["ended_at"]
    assert row["joinable"] is True and row["key_expires_at"] == created["key_expires_at"]
    assert row["summary"] == first.summary == {"output": "log.txt"}
    assert row["summary_truncated"] is False
    assert (row["artifact_count"], row["artifacts_available"]) == (1, 1)

    # the key's window ends: the same key starts a second run; rows are newest first and only
    # the newest one is joinable
    later = time.time() + 3 * HOUR_S
    monkeypatch.setattr(keys, "now", lambda: later)
    second = _send(kernel, "gate:1", "outputs_writer", {"name": "log.txt", "body": "x"})
    assert second.run_id != first.run_id
    rows = _rows(kernel, "gate:1")["items"]
    assert [r["run_id"] for r in rows] == [second.run_id, first.run_id]
    assert [r["joinable"] for r in rows] == [True, False]
    # an artifact GC collected shows in artifacts_available
    run_dir = find_run_dir(home, first.run_id)
    assert run_dir is not None
    shutil.rmtree(run_dir / "evidence" / "artifacts")
    old = _rows(kernel, "gate:1")["items"][1]
    assert (old["artifact_count"], old["artifacts_available"]) == (1, 0)


def test_run_by_key_failed_run_carries_its_error_code_and_class(tmp_path: Path) -> None:
    kernel = _kernel(_home(tmp_path))
    failed = _send(kernel, "gate:boom", "boom", {"why": "x"})
    assert failed.state == "failed"
    (row,) = _rows(kernel, "gate:boom")["items"]
    assert row["state"] == "failed" and row["outcome_class"] == failed.outcome["class"]
    assert row["error_code"] == codes.EXECUTION_PLUGIN_RAISED
    assert row["joinable"] is True  # a re-send of the same key returns this answer


def test_run_by_key_unknown_key_is_an_empty_page_and_a_missing_run_dir_is_omitted(
    tmp_path: Path,
) -> None:
    home = _home(tmp_path)
    kernel = _kernel(home)
    page = _rows(kernel, "never-used")
    assert page["items"] == [] and page["truncated"] is False and page["next_cursor"] is None
    only = _send(kernel, "gate:gone")
    keep = _send(kernel, "gate:half")
    run_dir = find_run_dir(home, only.run_id)
    assert run_dir is not None
    shutil.rmtree(run_dir)  # a claim whose run directory is gone (before its commit rename, or GC)
    assert _rows(kernel, "gate:gone")["items"] == []
    assert [r["run_id"] for r in _rows(kernel, "gate:half")["items"]] == [keep.run_id]
    bad = kernel.control.query("run_by_key", {})
    assert isinstance(bad, RequestOutcome) and bad.code == codes.PROJECTION_INVALID_ARGS


def test_run_by_key_finds_a_run_far_beyond_the_recency_window_and_takes_no_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _home(tmp_path)
    kernel = _kernel(home)
    sent = {f"gate:{n}": _send(kernel, f"gate:{n}", args={"message": f"m{n}"}) for n in range(4)}
    monkeypatch.setattr(view_defs, "RECENCY_CACHE_SIZE", 2)
    recent = kernel.control.query("recent_runs", {})
    assert isinstance(recent, dict) and recent["truncated"] is True  # the window is incomplete
    # the window is newest first: take a run it left out
    listed = {r["run_id"] for r in recent["items"]}
    key, old = next((k, v) for k, v in sent.items() if v.run_id not in listed)
    # run_by_key never reads the window: the left-out run is found, and the page is not truncated
    page = _rows(kernel, key)
    assert [r["run_id"] for r in page["items"]] == [old.run_id] and page["truncated"] is False
    # ... and while another process holds the admission lock the read still answers at once
    answered: list[dict[str, Any]] = []
    with admission_lock(home):
        reader = threading.Thread(target=lambda: answered.append(_rows(kernel, key)))
        reader.start()
        reader.join(timeout=JOIN_S)
        assert not reader.is_alive(), "run_by_key waited for the admission lock"
    assert answered[0]["items"][0]["run_id"] == old.run_id
    # the bare backend (no Project) answers the same rows' identity
    bare = FilesystemQueryBackend(home).query("run_by_key", {"idempotency_key": key})
    assert isinstance(bare, dict) and bare["items"][0]["run_id"] == old.run_id


# --- await_runs by key ---------------------------------------------------------------------------


def test_await_runs_by_key_resolves_the_newest_run_and_labels_only_key_views(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _home(tmp_path)
    kernel = _kernel(home)
    plain = _send(kernel, "plain:1")
    first = _send(kernel, "gate:k")
    monkeypatch.setattr(keys, "now", lambda: time.time() + 3 * HOUR_S)
    newest = _send(kernel, "gate:k")
    assert newest.run_id != first.run_id
    # no keys: the same bytes as today's answer for a run id
    by_id = kernel.control.await_runs([plain.run_id])
    assert isinstance(by_id, list)
    status = kernel.control.project.status(plain.run_id)
    assert json.dumps(by_id[0].to_dict(), sort_keys=True) == json.dumps(
        status.to_dict(), sort_keys=True
    )
    assert "idempotency_key" not in by_id[0].to_dict()
    # run ids first, then keys, each in the order given; a key view carries its key
    views = kernel.control.await_runs(
        [plain.run_id], keys=["gate:k", "plain:1"], timeout_ms=RUN_WAIT_MS
    )
    assert isinstance(views, list)
    assert [v.run_id for v in views] == [plain.run_id, newest.run_id, plain.run_id]
    assert [v.to_dict().get("idempotency_key") for v in views] == [None, "gate:k", "plain:1"]
    assert all(v.state == "succeeded" for v in views)
    only_keys = kernel.control.await_runs(keys=["plain:1"])
    assert isinstance(only_keys, list) and only_keys[0].idempotency_key == "plain:1"
    # the async path (the MCP tool's) answers the same
    awaited = asyncio.run(kernel.control.await_runs_async(keys=["gate:k"], mode="any"))
    assert isinstance(awaited, list) and awaited[0].run_id == newest.run_id


def test_await_runs_refuses_neither_and_an_unknown_key_by_name(tmp_path: Path) -> None:
    kernel = _kernel(_home(tmp_path))
    done = _send(kernel, "gate:real")
    neither = kernel.control.await_runs([])
    assert isinstance(neither, RequestOutcome) and neither.code == codes.PROJECTION_INVALID_ARGS
    neither = kernel.control.await_runs(None, keys=None)
    assert isinstance(neither, RequestOutcome) and neither.code == codes.PROJECTION_INVALID_ARGS
    unknown = kernel.control.await_runs([done.run_id], keys=["gate:real", "gate:nope"])
    assert isinstance(unknown, RequestOutcome) and unknown.code == codes.PROJECTION_UNKNOWN_KEY
    assert "gate:nope" in unknown.message and unknown.retryable is False
    refused = asyncio.run(kernel.control.await_runs_async(keys=["gate:nope"]))
    assert isinstance(refused, RequestOutcome) and refused.code == codes.PROJECTION_UNKNOWN_KEY


def test_the_mcp_tools_carry_the_new_arguments_and_view(tmp_path: Path) -> None:
    with mcp_host.McpHost(home=tmp_path / "home") as host:
        tools = {
            t["name"]: t["inputSchema"]["properties"]
            for t in json.loads(host.tools_list_raw())["result"]["tools"]
        }
        assert {"deadline_s", "idempotency_ttl_s"} <= set(tools["run"])
        assert {"run_ids", "keys"} <= set(tools["await_runs"])
        assert "run_by_key" in tools["query"]["view"]["enum"]
        done = host.call(
            "run",
            {
                "plugin": "echo",
                "args": {"message": "w"},
                "idempotency_key": "wire:1",
                "completion": "terminal",
                "idempotency_ttl_s": 7200,
            },
        )
        assert done["state"] == "succeeded" and done["deadline_s"] == 300
        rows = host.call("query", {"view": "run_by_key", "params": {"idempotency_key": "wire:1"}})[
            "items"
        ]
        assert [r["run_id"] for r in rows] == [done["run_id"]]
        unknown = host.call("await_runs", {"run_ids": [], "keys": ["wire:none"]})["result"]
        assert unknown["code"] == codes.PROJECTION_UNKNOWN_KEY
        # the published schema keeps run_ids required (golden S0); keys alone is run_ids: []
        neither = host.call("await_runs", {"run_ids": []})["result"]
        assert neither["code"] == codes.PROJECTION_INVALID_ARGS
        alone = host.call("await_runs", {"run_ids": [], "keys": ["wire:1"]})["result"]
        assert alone[0]["idempotency_key"] == "wire:1"


# --- idempotency_ttl_s ---------------------------------------------------------------------------


def test_ttl_out_of_range_is_refused_before_a_run_id(tmp_path: Path) -> None:
    home = _home(tmp_path)
    (home / "config.toml").write_text("[keys]\nmax_ttl_s = 1000\n")
    kernel = _kernel(home)
    for bad in (-1, 1.5, 1001, True, float("nan")):
        refused = kernel.control.run(
            "echo", {"message": "t"}, idempotency_key="t:bad", idempotency_ttl_s=bad
        )
        assert isinstance(refused, RequestOutcome), bad
        assert refused.code == codes.ADMISSION_TTL_OUT_OF_RANGE, bad
        assert refused.retryable is False and "run_id" not in refused.to_dict()
    assert not (home / "runs").exists() or not list((home / "runs").glob("*/*"))
    assert keys.read_entries(home, "t:bad") == []
    ok = _send(kernel, "t:max", idempotency_ttl_s=1000)  # the maximum itself is valid
    assert _created(home, ok.run_id)["idempotency_ttl_s"] == 1000
    assert (
        _created(home, _send(kernel, "t:int", idempotency_ttl_s=600.0).run_id)["idempotency_ttl_s"]
        == 600
    )


def test_ttl_zero_is_valid_and_ttl_is_fixed_into_key_expires_at(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRESTLE_IDEMPOTENCY_TTL_S", "111")
    home = _home(tmp_path)
    kernel = _kernel(home)
    zero = _send(kernel, "ttl:0", idempotency_ttl_s=0)
    omitted = _send(kernel, "ttl:omitted")
    day = _send(kernel, "ttl:day", idempotency_ttl_s=DAY_S)
    window = 300 + clock.finalization_margin
    for run, ttl in ((zero, 0), (omitted, 111), (day, DAY_S)):
        created = _created(home, run.run_id)
        assert created["idempotency_ttl_s"] == ttl
        entry = keys.read_entries(home, created["idempotency_key"])[0]
        assert entry.key_expires_at == created["key_expires_at"]
        at = keys.now()
        assert abs(created["key_expires_at"] - at - (window + ttl)) <= 5.0
    # ttl 0: the key joins only while the run lives (until its deadline plus margin)
    assert keys.lookup(home, "ttl:0", at_time=keys.now() + window - 5) is not None
    assert keys.lookup(home, "ttl:0", at_time=keys.now() + window + 5) is None


def test_the_maximum_comes_from_config_at_each_admission_and_is_checked_at_load(
    tmp_path: Path,
) -> None:
    home = _home(tmp_path)
    kernel = _kernel(home)
    assert load_config(home).max_ttl_s == 604800  # the default: 7 days
    assert _send(kernel, "m:1", idempotency_ttl_s=604800).state == "succeeded"
    refused = kernel.control.run(
        "echo", {"message": "m"}, idempotency_key="m:2", idempotency_ttl_s=604801
    )
    assert isinstance(refused, RequestOutcome) and refused.code == codes.ADMISSION_TTL_OUT_OF_RANGE
    (home / "config.toml").write_text("[keys]\nmax_ttl_s = 120\n")  # no restart
    assert _send(kernel, "m:3", idempotency_ttl_s=120).state == "succeeded"
    refused = kernel.control.run(
        "echo", {"message": "m"}, idempotency_key="m:4", idempotency_ttl_s=121
    )
    assert isinstance(refused, RequestOutcome) and refused.code == codes.ADMISSION_TTL_OUT_OF_RANGE
    # a maximum above metadata_days is refused at load
    (home / "config.toml").write_text(
        "[retention]\nmetadata_days = 2\n[keys]\nmax_ttl_s = 172801\n"
    )
    with pytest.raises(ValueError, match="max_ttl_s"):
        load_config(home)
    (home / "config.toml").write_text(
        "[retention]\nmetadata_days = 2\n[keys]\nmax_ttl_s = 172800\n"
    )
    assert load_config(home).max_ttl_s == 172800
    # an unset maximum never makes a short retention invalid: it is held to metadata_days
    (home / "config.toml").write_text("[retention]\nmetadata_days = 2\n")
    assert load_config(home).max_ttl_s == 172800


def test_a_long_ttl_joins_after_the_default_window_would_have_expired(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = _home(tmp_path)
    kernel = _kernel(home)
    short = _send(kernel, "reuse:short")
    long = _send(kernel, "reuse:long", idempotency_ttl_s=3 * DAY_S)
    two_hours_on = time.time() + 2 * HOUR_S + 300 + clock.finalization_margin
    monkeypatch.setattr(keys, "now", lambda: two_hours_on)
    again_long = _send(kernel, "reuse:long")  # joins: the recorded answer, no new run
    assert again_long.run_id == long.run_id
    again_short = _send(kernel, "reuse:short")  # the default window (1 h) is over: a new run
    assert again_short.run_id != short.run_id
    assert [r["joinable"] for r in _rows(kernel, "reuse:long")["items"]] == [True]
    assert [r["joinable"] for r in _rows(kernel, "reuse:short")["items"]] == [True, False]
