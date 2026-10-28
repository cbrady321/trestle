"""CS-2 recovery (L.CS-2.4; B2-C11, CSC-14, V-2.3): before `interrupted`, recovery reads the run's
`process_identity` rows and takes one branch. (i) no identity row: no signal, unconfirmed. (ii) the
leader is gone: no signal, confirmed gone only for a provably gone group whose every recorded row
is gone or reused. (iii) a live leader with a matching start: every recorded row is start-guarded
and the attributable processes are stopped. Only (iii) signals. Tokens are compared as integers."""

from __future__ import annotations

import shutil
import signal
from pathlib import Path
from typing import Any

import pytest

from tests.core.spine import support
from tests.core.spine.support import START, FakeHost
from tests.pins.a_lifecycle import straddle
from tests.proof import harness, mcp_host, tolerances
from trestle.common import clock
from trestle.common.types import RunView
from trestle.server.ledger import RunLedger, ledger_path
from trestle.server.procident import Identity
from trestle.server.recovery import recover_run_dir, seed_interrupted_run

RECORDING_BOOT = "boot-a"
TERM = int(signal.SIGTERM)
KILL = int(signal.SIGKILL)


@pytest.fixture(autouse=True)
def short_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(clock, "grace", tolerances.SETTLE_SHORT_S)
    monkeypatch.setattr(clock, "kill", tolerances.SETTLE_SHORT_S)


# -- the real thing: a server SIGKILLed mid-run --------------------------------------------


def _await_view(host: mcp_host.McpHost, run_id: str) -> dict[str, Any]:
    answer = host.call(
        "await_runs", {"run_ids": [run_id], "mode": "all", "timeout_ms": tolerances.HARNESS_WAIT_MS}
    )
    views = answer["result"] if isinstance(answer, dict) and "result" in answer else answer
    (view,) = views
    return view


def _kill_server_mid_run(
    host: mcp_host.McpHost, monkeypatch: pytest.MonkeyPatch | None = None, **env: str
) -> tuple[str, Path, support.Tree]:
    shutil.copy(support.SPINE_PLUGIN_DIR / "tree.py", host.home / "plugins")
    started = host.call("run", {"plugin": "tree", "wait_ms": 0})
    run_id = started["run_id"]
    matches = sorted((host.home / "runs").glob(f"*/{run_id}"))
    assert matches
    run_dir = matches[0]
    support.wait_ready(run_dir)
    tree = support.Tree(marker=run_id, live=support.marked(run_id))
    assert tree.wrapper and tree.child and len(tree.descendants) == 2, tree.live
    assert support.wait_until(
        lambda: len(support.rows_of(run_dir, "process_identity")) >= 4, tolerances.JOIN_WAIT_S
    )
    host.kill_server()
    if monkeypatch is not None:
        for key, value in env.items():
            monkeypatch.setenv(key, value)
    host.restart()
    return run_id, run_dir, tree


@pytest.mark.proves("A6.2", "A6.2:core", "A", "core", "PROC", "BOTH")
@pytest.mark.proves(
    "WR-CANCEL-3", "WR-CANCEL-3:interrupted-never-succeeded", "core", "core", "PROC", "BOTH"
)
@pytest.mark.proves("WR-CANCEL-3", "WR-CANCEL-3:never-nonterminal", "core", "core", "PROC", "BOTH")
@pytest.mark.proves(
    "WR-CANCEL-3", "WR-CANCEL-3:process-alive-after-recovery", "core", "core", "PROC", "BOTH"
)
@pytest.mark.proves(
    "WR-OWN-1", "WR-OWN-1:recovery-ends-run-created-process", "core", "core", "PROC", "BOTH"
)
def test_server_killed_mid_run_recovery_stops_recorded_group() -> None:
    with mcp_host.McpHost() as host:
        run_id = ""
        try:
            run_id, run_dir, tree = _kill_server_mid_run(host)
            # the run is finalized interrupted (never succeeded, never left nonterminal) ...
            assert support.kinds(run_dir)[-1] == "interrupted"
            # ... every attributable process is gone by the time recovery finished ...
            assert support.wait_until(
                lambda: not support.alive_marked(tree.live, run_id), tolerances.PROC_WAIT_S
            )
            # ... and the record says so: a recovery stop, confirmed, cleanup released
            (row,) = support.rows_of(run_dir, "group_stop")
            assert (row["confirmed_gone"], row["method"]) == (True, "recovery")
            view = _await_view(host, run_id)
            assert view["state"] == "interrupted"
            assert view["cleanup"] == {"processes": "released"}
        finally:
            if run_id:
                from tests.proof import ancestry

                ancestry.reap(support.marked(run_id))


@pytest.mark.proves(
    "WR-CANCEL-3", "WR-CANCEL-3:signals-only-recorded-identity", "core", "core", "PROC", "BOTH"
)
def test_recovery_under_other_tz_and_locale_signals_live_leader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The restarted server runs under another TZ and LC_ALL than the one that recorded the
    identity: the tokens still match, so recovery still sees a live matching leader and stops it."""
    with mcp_host.McpHost() as host:
        run_id = ""
        try:
            run_id, run_dir, tree = _kill_server_mid_run(
                host, monkeypatch, TZ="Asia/Kolkata", LC_ALL="C"
            )
            assert support.wait_until(
                lambda: not support.alive_marked(tree.live, run_id), tolerances.PROC_WAIT_S
            )
            (row,) = support.rows_of(run_dir, "group_stop")
            assert (row["confirmed_gone"], row["method"]) == (True, "recovery")
            assert _await_view(host, run_id)["cleanup"] == {"processes": "released"}
        finally:
            if run_id:
                from tests.proof import ancestry

                ancestry.reap(support.marked(run_id))


# -- planted guard cases, through a planted process table and boot id, the signaller injected


def _identity(pid: int, group: int, *, leader: bool, start: int = START) -> Identity:
    return Identity(pid=pid, boot=RECORDING_BOOT, start=start, group=group, leader=leader)


def _recorded_run(host: FakeHost, *identities: Identity) -> tuple[Any, Path]:
    """A kernel over an empty home and a started run whose ledger holds `identities`."""
    kernel = harness.fresh_kernel(plugin_dirs=[])
    run_id = "r_recover_case"
    run_dir = seed_interrupted_run(kernel.home, run_id, last_kind="started")
    ledger = RunLedger.open(ledger_path(run_dir))
    for ident in identities:
        ledger.append("process_identity", run_id=run_id, **ident.fields())
    return kernel, run_dir


def _recover(kernel: Any, run_dir: Path, host: FakeHost) -> tuple[dict[str, Any], RunView]:
    recover_run_dir(run_dir, source=host, signaller=host)
    kinds = support.kinds(run_dir)
    assert kinds[-1] == "interrupted"  # never succeeded, never left nonterminal
    assert kinds.count("group_stop") == 1
    (row,) = support.rows_of(run_dir, "group_stop")
    view = kernel.control.project.status(run_dir.name)
    assert isinstance(view, RunView)
    return row, view


def _leader_and_members() -> tuple[Identity, Identity, Identity]:
    """leader 100 (group 100), member 101 (group 100), member 102 that left for its own group."""
    return (
        _identity(100, 100, leader=True),
        _identity(101, 100, leader=False),
        _identity(102, 102, leader=False),
    )


@pytest.mark.proves(
    "WR-CANCEL-3", "WR-CANCEL-3:signals-only-recorded-identity", "core", "core", "PROC", "BOTH"
)
def test_live_leader_reused_member_pid_not_signalled() -> None:
    """Branch (iii): the leader matches, a recorded member's pid now carries another start."""
    host = FakeHost()
    leader, member, leaver = _leader_and_members()
    host.add(100, 1, 100)
    host.add(101, 1, 101, start=START + 50)  # pid 101 reused by an unrelated process
    host.add(102, 100, 102)
    host.add(103, 102, 102)  # a live descendant with no row yet
    kernel, run_dir = _recorded_run(host, leader, member, leaver)
    row, view = _recover(kernel, run_dir, host)

    assert 101 not in {t for kind, t, *_ in host.sent if kind == "pid"}  # zero signals to it
    assert ("group", 101) not in {(kind, t) for kind, t, *_ in host.sent}
    assert 101 in host.rows  # untouched
    assert set(host.rows) == {101}  # every attributable process was stopped
    assert (row["confirmed_gone"], row["method"]) == (True, "recovery")  # the reused row: reused
    assert view.cleanup is not None and view.cleanup.processes == "released"
    # the descendant found now got its identity row before it was signalled
    assert 103 in {r["pid"] for r in support.rows_of(run_dir, "process_identity")}


@pytest.mark.proves(
    "WR-CANCEL-3", "WR-CANCEL-3:signals-only-recorded-identity", "core", "core", "PROC", "BOTH"
)
def test_reused_pid_not_signalled() -> None:
    """Branch (ii): the leader's pid now carries another start and members remain in the group."""
    host = FakeHost()
    leader, member, _ = _leader_and_members()
    host.add(100, 1, 100, start=START + 50)  # the leader pid, reused
    host.add(101, 100, 100)  # a member of the recorded group still lives
    kernel, run_dir = _recorded_run(host, leader, member)
    row, view = _recover(kernel, run_dir, host)

    assert host.sent == []  # zero signals
    assert (row["confirmed_gone"], row["method"]) == (False, "recovery")
    assert view.cleanup is not None and view.cleanup.processes == "unknown"


@pytest.mark.proves(
    "WR-CANCEL-3", "WR-CANCEL-3:signals-only-recorded-identity", "core", "core", "PROC", "BOTH"
)
@pytest.mark.proves(
    "WR-CANCEL-3", "WR-CANCEL-3:unconfirmed-never-clean-restart", "core", "core", "PROC", "BOTH"
)
def test_leader_gone_members_started_after_record_not_signalled() -> None:
    """Branch (ii): same boot, the leader is gone, every member of the group started after the
    recorded start. They are nobody's recorded process: zero signals, unconfirmed."""
    host = FakeHost()
    leader, member, _ = _leader_and_members()
    host.add(101, 1, 100, start=START + 500)  # in group 100, but started after the record
    host.add(104, 1, 100, start=START + 600)
    kernel, run_dir = _recorded_run(host, leader, member)
    row, view = _recover(kernel, run_dir, host)

    assert host.sent == []
    assert (row["confirmed_gone"], row["method"]) == (False, "recovery")
    assert view.cleanup is not None and view.cleanup.processes == "unknown"  # never clean
    assert set(host.rows) == {101, 104}


@pytest.mark.proves(
    "WR-CANCEL-3", "WR-CANCEL-3:signals-only-recorded-identity", "core", "core", "PROC", "BOTH"
)
def test_other_boot_confirmed_gone_nothing_signalled() -> None:
    """Branch (ii): the boot differs, so every recorded pid is another boot's: nothing is signalled
    (an unrelated process may carry the same pid) and the group is confirmed gone."""
    host = FakeHost()
    host.boot = "boot-b"
    leader, member, leaver = _leader_and_members()
    host.add(100, 1, 100)  # same pid and start as recorded, but on another boot: not the leader
    host.add(101, 100, 100)
    kernel, run_dir = _recorded_run(host, leader, member, leaver)
    row, view = _recover(kernel, run_dir, host)

    assert host.sent == []
    assert (row["confirmed_gone"], row["method"]) == (True, "recovery")
    assert view.cleanup is not None and view.cleanup.processes == "released"
    assert set(host.rows) == {100, 101}


@pytest.mark.proves(
    "WR-CANCEL-3", "WR-CANCEL-3:signals-only-recorded-identity", "core", "core", "PROC", "BOTH"
)
def test_same_boot_empty_pgid_confirmed_gone() -> None:
    """Branch (ii): the leader is gone, nothing is in the recorded group, every recorded row is
    gone or reused: confirmed gone, nothing signalled."""
    host = FakeHost()
    leader, member, leaver = _leader_and_members()
    host.add(102, 1, 999, start=START + 70)  # pid 102 reused by an unrelated process elsewhere
    kernel, run_dir = _recorded_run(host, leader, member, leaver)
    row, view = _recover(kernel, run_dir, host)

    assert host.sent == []
    assert (row["confirmed_gone"], row["method"]) == (True, "recovery")
    assert view.cleanup is not None and view.cleanup.processes == "released"


@pytest.mark.proves(
    "WR-CANCEL-3", "WR-CANCEL-3:signals-only-recorded-identity", "core", "core", "PROC", "BOTH"
)
@pytest.mark.proves(
    "WR-CANCEL-3", "WR-CANCEL-3:unconfirmed-never-clean-restart", "core", "core", "PROC", "BOTH"
)
def test_same_boot_empty_pgid_live_recorded_row_unconfirmed() -> None:
    """Branch (ii): the group is empty, but a recorded process that left the group is alive with
    its recorded start: nothing is signalled (only branch (iii) signals) and it is unconfirmed."""
    host = FakeHost()
    leader, member, leaver = _leader_and_members()
    host.add(102, 1, 102)  # the recorded process that left the group, alive, start matching
    kernel, run_dir = _recorded_run(host, leader, member, leaver)
    row, view = _recover(kernel, run_dir, host)

    assert host.sent == []
    assert (row["confirmed_gone"], row["method"]) == (False, "recovery")
    assert view.cleanup is not None and view.cleanup.processes == "unknown"
    assert 102 in host.rows


class _SpySource(FakeHost):
    """Records that nothing read the process table: branch (i) needs no process to compare."""


@pytest.mark.proves(
    "WR-CANCEL-3",
    "WR-CANCEL-3:s0-straddle-stop-unconfirmed-never-clean",
    "core",
    "core",
    "PROC",
    "BOTH",
)
def test_straddle_s0_run_unconfirmed_never_clean(tmp_path: Path) -> None:
    """Branch (i), K-19: the F-E4-1 fossil (an S0-started run, no identity row) and a live process
    carrying the S0 argv marker. The signaller gets zero calls and nothing reads or scans the
    process table; the stop is unconfirmed, the run ends interrupted, cleanup never clean."""
    spy = _SpySource()
    run_dir = straddle.straddle_run_dir(tmp_path)
    with straddle.spawn_s0_shaped_orphan(run_dir) as orphan:
        recover_run_dir(run_dir, source=spy, signaller=spy)
        assert spy.sent == []  # the signaller receives zero calls
        assert spy.reads == 0  # and nothing scans the table (or argv) to look for the orphan
        assert straddle.is_alive(orphan)
        kinds = support.kinds(run_dir)
        assert kinds[-1] == "interrupted"
        (row,) = support.rows_of(run_dir, "group_stop")
        assert (row["confirmed_gone"], row["method"]) == (False, "no_identity")
        kernel = harness.fresh_kernel(plugin_dirs=[], home=run_dir.parents[2])
        view = kernel.control.project.status(run_dir.name)
        assert isinstance(view, RunView)
        assert view.cleanup is not None and view.cleanup.processes == "unknown"
        assert view.state == "interrupted"


def test_recovery_is_idempotent_and_writes_one_group_stop() -> None:
    host = FakeHost()
    leader, member, _ = _leader_and_members()
    host.add(100, 1, 100)
    host.add(101, 100, 100)
    kernel, run_dir = _recorded_run(host, leader, member)
    _recover(kernel, run_dir, host)
    before = support.kinds(run_dir)
    recover_run_dir(run_dir, source=host, signaller=host)  # a second pass changes nothing
    assert support.kinds(run_dir) == before
    assert before.count("group_stop") == 1


def test_a_run_that_never_started_gets_no_group_stop() -> None:
    kernel = harness.fresh_kernel(plugin_dirs=[])
    run_dir = seed_interrupted_run(kernel.home, "r_never_started", last_kind="admitted")
    host = FakeHost()
    recover_run_dir(run_dir, source=host, signaller=host)
    assert "group_stop" not in support.kinds(run_dir)
    assert support.kinds(run_dir)[-1] == "interrupted"
    assert host.sent == [] and host.reads == 0
