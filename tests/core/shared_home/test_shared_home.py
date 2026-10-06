"""v0.4 Problem A, step 1a: N server processes are safe on one home.

Two-process tests run `trestle serve` subprocesses (`tests.proof.mcp_host.McpHost`) on one
`TRESTLE_HOME`; the rest drive the same locks in process. Each server's stop bounds are shortened
(`TRESTLE_CANCEL_GRACE_S=0`, `TRESTLE_CANCEL_KILL_S=1`) so a reaped run stops at once.
"""

from __future__ import annotations

import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from tests.proof import mcp_host
from trestle import cli
from trestle.common import codes
from trestle.common.types import AdmitRequest, RequestOutcome, RunView
from trestle.server import admission as admission_mod
from trestle.server import home as home_mod
from trestle.server.config import RetentionConfig, TrestleConfig
from trestle.server.doctor import build_doctor_report
from trestle.server.gc import run_gc
from trestle.server.home import (
    GC_LOCK,
    HomeRefused,
    admission_holder,
    file_lock,
    home_format,
    is_locked,
    live_run_ids,
    live_servers,
    locks_dir,
    marker_path,
    owner_lock_path,
    read_marker,
)
from trestle.server.idempotency import IdempotencyStore, rebuild_from_ledgers
from trestle.server.ledger import RunLedger, iter_run_dirs, ledger_path, work_dir
from trestle.server.main import create_kernel
from trestle.server.reaper import REAP_INTERVAL_S, reap_home
from trestle.server.recovery import find_run_dir

REPO = Path(__file__).resolve().parents[3]
PLUGINS = Path(__file__).resolve().parent / "plugins"
FIXTURE_PLUGINS = REPO / "tests" / "fixtures" / "plugins"
V030_HOME = REPO / "tests" / "fixtures" / "fossils" / "core" / "core-admitted" / "home"
# a run that outlives every test here unless it is stopped
LONG_S = 60
WAIT_S = 15.0
# the reaped run's stop: grace 0 plus kill 1, and room for the finalization writes
STOP_S = 3.0
POLL_S = 0.05
RUN_WAIT_MS = 5000
NO_WAIT_MS = 0

_HOLD_ADMISSION_LOCK = """
import sys, time
from pathlib import Path
from trestle.server.home import admission_lock
with admission_lock(Path(sys.argv[1])):
    print("held", flush=True)
    time.sleep(30)
"""


@pytest.fixture
def fast_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every server started from here stops a run at once (grace 0, kill 1 s)."""
    monkeypatch.setenv("TRESTLE_CANCEL_GRACE_S", "0")
    monkeypatch.setenv("TRESTLE_CANCEL_KILL_S", "1")


def _home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    (home / "plugins").mkdir(parents=True)
    for path in PLUGINS.glob("*.py"):
        shutil.copy(path, home / "plugins" / path.name)
    return home


def _wait(predicate: Callable[[], bool], bound_s: float = WAIT_S) -> bool:
    deadline = time.monotonic() + bound_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(POLL_S)
    return predicate()


def _run_dir(home: Path, run_id: str) -> Path:
    found = find_run_dir(home, run_id)
    assert found is not None, run_id
    return found


def _kinds(run_dir: Path) -> list[str]:
    return [str(row.get("kind")) for row in RunLedger.open(ledger_path(run_dir)).records]


def _started(home: Path, run_id: str) -> None:
    run_dir = _run_dir(home, run_id)
    assert _wait(lambda: "started" in _kinds(run_dir)), _kinds(run_dir)


def _views(answer: Any) -> list[dict[str, Any]]:
    return answer["result"] if isinstance(answer, dict) and "result" in answer else answer


def _refused_draining(host: mcp_host.McpHost) -> bool:
    answer = host.call("run", {"plugin": "echo", "args": {"message": "late"}, "wait_ms": 0})
    return answer.get("code") == codes.SERVICE_DRAINING


# --- two processes -------------------------------------------------------------------------------


def test_killed_server_is_reaped_by_the_other_within_10s(tmp_path: Path, fast_stop: None) -> None:
    home = _home(tmp_path)
    with mcp_host.McpHost(home=home) as a, mcp_host.McpHost(home=home) as b:
        run = a.call("run", {"plugin": "slow", "args": {"seconds": LONG_S}, "wait_ms": 0})
        run_id = run["run_id"]
        _started(home, run_id)
        run_dir = _run_dir(home, run_id)
        assert is_locked(owner_lock_path(run_dir))
        a.kill_server()
        killed = time.monotonic()
        # b's periodic reaper (every 10 s) takes it: nothing here waits on the run through b
        assert _wait(lambda: "interrupted" in _kinds(run_dir), REAP_INTERVAL_S + STOP_S)
        assert time.monotonic() - killed <= REAP_INTERVAL_S + STOP_S
        assert _wait(lambda: not marker_path(home, run_id).exists(), STOP_S)
        (view,) = _views(b.call("await_runs", {"run_ids": [run_id], "timeout_ms": 1000}))
        assert view["state"] == "interrupted"
        assert _kinds(run_dir).count("interrupted") == 1


def test_starting_a_server_never_touches_a_live_servers_runs(
    tmp_path: Path, fast_stop: None
) -> None:
    home = _home(tmp_path)
    with mcp_host.McpHost(home=home) as a:
        run_id = a.call("run", {"plugin": "slow", "args": {"seconds": LONG_S}, "wait_ms": 0})[
            "run_id"
        ]
        _started(home, run_id)
        run_dir = _run_dir(home, run_id)
        with mcp_host.McpHost(home=home):  # its start pass has run once this returns
            assert reap_home(home).reaped == []  # and `trestle recover`'s pass leaves it too
            kinds = _kinds(run_dir)
            assert "group_stop" not in kinds and "interrupted" not in kinds, kinds
            assert marker_path(home, run_id).exists()
        assert a.call("cancel", {"run_id": run_id})["code"] == codes.CANCEL_ACCEPTED
        (view,) = _views(a.call("await_runs", {"run_ids": [run_id], "timeout_ms": 10000}))
        assert view["state"] == "cancelled"
        assert _wait(lambda: not marker_path(home, run_id).exists())


def test_two_servers_sending_one_key_at_once_create_one_run(tmp_path: Path) -> None:
    home = _home(tmp_path)
    with mcp_host.McpHost(home=home) as a, mcp_host.McpHost(home=home) as b:
        for n in range(4):
            args = {
                "plugin": "echo",
                "args": {"message": f"once-{n}"},
                "idempotency_key": f"one-key-{n}",
                "wait_ms": 0,
            }
            held = [(a, a.hold("run", args)), (b, b.hold("run", args))]
            answers = [host.join(req_id) for host, req_id in held]
            assert answers[0]["run_id"] == answers[1]["run_id"], answers
        assert len(list(iter_run_dirs(home))) == 4


def test_reaped_run_keeps_its_log_only_without_secret_values(
    tmp_path: Path, fast_stop: None
) -> None:
    home = _home(tmp_path)
    with mcp_host.McpHost(home=home) as a, mcp_host.McpHost(home=home) as b:
        plain = a.call("run", {"plugin": "gate_log", "args": {"seconds": LONG_S}, "wait_ms": 0})
        secret = a.call(
            "run",
            {
                "plugin": "gate_log",
                "args": {"seconds": LONG_S, "token": "s3cret-value"},
                "wait_ms": 0,
            },
        )
        run_ids = [plain["run_id"], secret["run_id"]]
        for run_id in run_ids:
            _started(home, run_id)
            log = work_dir(_run_dir(home, run_id)) / "outputs" / "pytest.log"
            assert _wait(log.exists)
        a.kill_server()
        # b's waiter finds the owner locks free and wakes b's reaper
        views = _views(
            b.call("await_runs", {"run_ids": run_ids, "mode": "all", "timeout_ms": 20000})
        )
        assert [view["state"] for view in views] == ["interrupted", "interrupted"], views
        listed = b.call("query", {"view": "run_artifacts", "params": {"run_id": run_ids[0]}})
        assert [item["name"] for item in listed["items"]] == ["pytest.log"], listed
        none = b.call("query", {"view": "run_artifacts", "params": {"run_id": run_ids[1]}})
        assert none["items"] == [], none


def test_sigterm_drain_finishes_admitted_runs_and_refuses_new(
    tmp_path: Path, fast_stop: None
) -> None:
    home = _home(tmp_path)
    (home / "config.toml").write_text("max_running_runs = 1\n", encoding="utf-8")
    with mcp_host.McpHost(home=home) as a:
        first = a.call("run", {"plugin": "slow", "args": {"seconds": 2}, "wait_ms": 0})
        queued = a.call("run", {"plugin": "echo", "args": {"message": "queued"}, "wait_ms": 0})
        a.proc.send_signal(signal.SIGTERM)
        assert _wait(lambda: _refused_draining(a), 5.0)
        views = _views(
            a.call(
                "await_runs",
                {"run_ids": [first["run_id"], queued["run_id"]], "timeout_ms": 15000},
            )
        )
        assert [view["state"] for view in views] == ["succeeded", "succeeded"], views
        assert a.proc.poll() is None  # a drain never exits on its own
        assert live_run_ids(home) == []


def test_server_killed_mid_drain_leaves_its_runs_to_the_other(
    tmp_path: Path, fast_stop: None
) -> None:
    home = _home(tmp_path)
    with mcp_host.McpHost(home=home) as a, mcp_host.McpHost(home=home) as b:
        run_id = a.call("run", {"plugin": "slow", "args": {"seconds": LONG_S}, "wait_ms": 0})[
            "run_id"
        ]
        _started(home, run_id)
        a.proc.send_signal(signal.SIGTERM)
        assert _wait(lambda: _refused_draining(a), 5.0)
        a.kill_server()
        (view,) = _views(b.call("await_runs", {"run_ids": [run_id], "timeout_ms": 20000}))
        assert view["state"] == "interrupted"


def test_admission_lock_held_past_2s_is_home_busy_and_doctor_names_holder(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    kernel = create_kernel(home=home, plugin_dirs=[FIXTURE_PLUGINS], skip_recovery=True)
    holder = subprocess.Popen(
        [sys.executable, "-c", _HOLD_ADMISSION_LOCK, str(home)],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None and holder.stdout.readline().strip() == "held"
        began = time.monotonic()
        refused = kernel.control.run(plugin="echo", args={"message": "busy"}, wait_ms=NO_WAIT_MS)
        waited = time.monotonic() - began
        assert isinstance(refused, RequestOutcome), refused
        assert (refused.code, refused.retryable) == (codes.ADMISSION_HOME_BUSY, True)
        assert home_mod.ADMISSION_WAIT_S <= waited < home_mod.ADMISSION_WAIT_S + 3
        assert list(iter_run_dirs(home)) == []  # nothing was minted
        report = build_doctor_report(home=home, plugin_dirs=[FIXTURE_PLUGINS])
        assert report.admission_holder is not None
        assert f"pid={holder.pid}" in report.admission_holder
        assert f"admission_lock: held by {report.admission_holder}" in report.lines()
    finally:
        holder.kill()
        holder.wait()
    assert admission_holder(home) is None
    view = kernel.control.run(plugin="echo", args={"message": "free"}, wait_ms=RUN_WAIT_MS)
    assert isinstance(view, RunView) and view.state == "succeeded"


# --- in process ----------------------------------------------------------------------------------


class _Crash(Exception):
    pass


@pytest.mark.parametrize("step", ["a", "b", "spec", "created", "key", "marker", "d"])
def test_crash_at_each_admission_step_leaves_no_run_or_a_reapable_one(
    step: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    kernel = create_kernel(home=home, plugin_dirs=[FIXTURE_PLUGINS], skip_recovery=True)

    def crash(at: str) -> None:
        if at == step:
            raise _Crash(at)

    monkeypatch.setattr(admission_mod, "_admission_step", crash)
    request = AdmitRequest(plugin="echo", args={"message": step}, idempotency_key=f"k-{step}")
    with pytest.raises(_Crash):
        kernel.control.admission.admit(request)
    monkeypatch.setattr(admission_mod, "_admission_step", lambda at: None)

    visible = list(iter_run_dirs(home))
    assert len(visible) == (1 if step == "d" else 0), visible
    for run_dir in visible:  # a visible run has a ledger and a dead owner: reapable
        assert ledger_path(run_dir).exists()
        assert not is_locked(owner_lock_path(run_dir))
    assert admission_holder(home) is None

    report = reap_home(home)
    assert list((home / "runs").glob("*/.adm-*")) == []
    assert live_run_ids(home) == []
    for run_dir in visible:
        assert RunLedger.open(ledger_path(run_dir)).projected_state() == "interrupted"
    assert report.reaped == [run_dir.name for run_dir in visible]

    after = kernel.control.run(plugin="echo", args={"message": "after"}, wait_ms=RUN_WAIT_MS)
    assert isinstance(after, RunView) and after.state == "succeeded"


def test_gc_query_and_key_rebuild_never_touch_an_admission_in_flight(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    kernel = create_kernel(home=home, plugin_dirs=[FIXTURE_PLUGINS], skip_recovery=True)
    done = kernel.control.run(plugin="echo", args={"message": "old"}, wait_ms=RUN_WAIT_MS)
    assert isinstance(done, RunView) and done.state == "succeeded"
    seen: dict[str, Path] = {}
    keep_nothing = TrestleConfig(
        retention=RetentionConfig(metadata_days=0, artifact_days=0, abandoned_hours=0)
    )

    def during(at: str) -> None:
        if at != "created":  # the run is built, owner-locked and not yet visible
            return
        (building,) = (home / "runs").glob("*/.adm-*")
        seen["building"] = building
        before = sorted(p.relative_to(building) for p in building.rglob("*"))
        report = run_gc(home, keep_nothing)
        assert report.runs_removed == 1  # the finished run goes; the one in flight stays
        listed = kernel.control.query("recent_runs", {})
        assert isinstance(listed, dict)
        assert all(not str(item["run_id"]).startswith(".") for item in listed["items"])
        rebuild_from_ledgers(home, ttl_s=60)
        assert "in-flight-key" not in IdempotencyStore.open(home).entries
        assert sorted(p.relative_to(building) for p in building.rglob("*")) == before

    monkeypatch.setattr(admission_mod, "_admission_step", during)
    result = kernel.control.admission.admit(
        AdmitRequest(plugin="echo", args={"message": "new"}, idempotency_key="in-flight-key")
    )
    assert result.tag == "admitted", result
    assert not seen["building"].exists()  # renamed into place
    run_dir = _run_dir(home, result.run_id)
    created = RunLedger.open(ledger_path(run_dir)).last_kind("created")
    assert created is not None
    assert (created["owner"], created["has_secrets"]) == (kernel.server_id, False)
    assert read_marker(home, result.run_id) is not None
    assert kernel.ownership is not None and kernel.ownership.owns(result.run_id)
    assert is_locked(owner_lock_path(run_dir))


def test_v030_home_is_refused_until_init_upgrade(tmp_path: Path) -> None:
    home = tmp_path / "home"
    shutil.copytree(V030_HOME, home)
    with pytest.raises(HomeRefused, match="init --upgrade"):
        create_kernel(home=home, plugin_dirs=[FIXTURE_PLUGINS], skip_recovery=True)
    for argv in (["serve"], ["doctor"], ["recover"], ["pin", "r_x"], ["unpin", "r_x"], ["init"]):
        assert cli.main([*argv, "--home", str(home)]) == 2, argv
    assert home_format(home) is None
    (old_run,) = iter_run_dirs(home)
    assert RunLedger.open(ledger_path(old_run)).projected_state() == "queued"

    assert cli.main(["init", "--upgrade", "--no-seed", "--home", str(home)]) == 0
    assert home_format(home) == 2
    assert live_run_ids(home) == [old_run.name]
    assert (read_marker(home, old_run.name) or {}).get("owner") is None
    # a v0.4 start reaps the old run: it has no owner, and nothing of it runs
    create_kernel(home=home, plugin_dirs=[FIXTURE_PLUGINS])
    assert RunLedger.open(ledger_path(old_run)).projected_state() == "interrupted"
    assert live_run_ids(home) == []


def test_network_mount_is_refused_at_every_entry_point(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    monkeypatch.setattr(home_mod, "fs_type", lambda path: "smbfs")
    with pytest.raises(HomeRefused, match="smbfs"):
        create_kernel(home=home, plugin_dirs=[FIXTURE_PLUGINS], skip_recovery=True)
    for argv in (["init"], ["doctor"], ["recover"], ["pin", "r_x"]):
        assert cli.main([*argv, "--home", str(home)]) == 2, argv
    assert home_format(home) is None


def test_gc_runs_one_at_a_time_and_keeps_snapshots_live_servers_serve(tmp_path: Path) -> None:
    home = tmp_path / "home"
    kernel = create_kernel(home=home, plugin_dirs=[FIXTURE_PLUGINS], skip_recovery=True)
    snapshots = {path.name for path in (home / "snapshots").iterdir()}
    assert snapshots
    with file_lock(locks_dir(home) / GC_LOCK) as held:
        assert held
        assert run_gc(home).skipped
    kernel.start_service()
    try:
        assert [row["server_id"] for row in live_servers(home)] == [kernel.server_id]
        assert run_gc(home).snapshots_removed == 0  # no run names them; the server serves them
        assert {path.name for path in (home / "snapshots").iterdir()} == snapshots
    finally:
        assert kernel.reaper is not None and kernel.server_lock is not None
        kernel.reaper.stop()
        kernel.server_lock.close()
    assert live_servers(home) == []
    assert run_gc(home).snapshots_removed == len(snapshots)


def test_pool_size_is_read_from_config_at_each_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("TRESTLE_MAX_RUNNING_RUNS", "7")  # ignored, and doctor says so
    kernel = create_kernel(home=home, plugin_dirs=[FIXTURE_PLUGINS], skip_recovery=True)
    (home / "config.toml").write_text("max_running_runs = 2\n", encoding="utf-8")
    view = kernel.control.run(plugin="echo", args={"message": "pool"}, wait_ms=RUN_WAIT_MS)
    assert isinstance(view, RunView) and view.state == "succeeded"
    assert kernel.control.scheduler.max_running == 2
    report = build_doctor_report(home=home, plugin_dirs=[FIXTURE_PLUGINS])
    assert any("TRESTLE_MAX_RUNNING_RUNS" in warning for warning in report.warnings)


def test_concurrent_publish_answers_registry_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    kernel = create_kernel(home=home, plugin_dirs=[plugins], skip_recovery=True)
    registry = kernel.registry
    source = "from trestle.plugin.surface import Context, trestle\n\n\n@trestle\n"
    ours = source + "def raced(ctx: Context) -> dict[str, int]:\n    return {'n': 1}\n"
    theirs = source + "def raced(ctx: Context) -> dict[str, int]:\n    return {'n': 2}\n"
    real_refresh = registry.refresh

    def other_server_wins() -> None:
        (plugins / "raced.py").write_text(theirs, encoding="utf-8")  # written after ours
        real_refresh()

    monkeypatch.setattr(registry, "refresh", other_server_wins)
    outcome = registry.publish_source(ours, name="raced")
    assert isinstance(outcome, RequestOutcome), outcome
    assert outcome.code == codes.PUBLICATION_REGISTRY_CONFLICT
    snap = registry.get("raced")
    assert snap is not None and snap.snapshot_id in outcome.message
    before = registry.registry_version
    monkeypatch.setattr(registry, "refresh", real_refresh)
    (plugins / "other.py").write_text(
        source + "def other(ctx: Context) -> dict[str, int]:\n    return {}\n", encoding="utf-8"
    )
    registry.refresh()
    assert registry.registry_version == before + 1  # one counter file for the home
    assert (home / "registry_version").read_text(encoding="utf-8").strip() == str(before + 1)
