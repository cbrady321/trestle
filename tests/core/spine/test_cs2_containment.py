"""CS-2 containment (L.CS-2.2; B2-C10, V-2.3): one group, one grace+kill stopper, no wrapper
timer. A cancel reaches the whole attributable tree: the child keeps the wrapper's group, and a
process that left the group (its own session, or reparented to init) is reached through its
identity row. SIGTERM comes first and SIGKILL only after `grace`."""

from __future__ import annotations

import inspect
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from tests.core.spine import support
from tests.proof import ancestry, tolerances
from trestle.common import clock
from trestle.server import procident
from trestle.server.procident import Attribution
from trestle.wrapper import main as wrapper_main
from trestle.wrapper import reactor

REPO = Path(__file__).resolve().parents[3]


@pytest.fixture
def short_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stop that keeps the suite fast: both bounds read at call time from clock.py."""
    monkeypatch.setattr(clock, "grace", support.TEST_GRACE_S)
    monkeypatch.setattr(clock, "kill", support.TEST_KILL_S)


def _cancel_and_join(kernel: Any, run_id: str, thread: threading.Thread) -> float:
    """Cancel through the control surface (the request path signals through the one stopper too)
    and return when that call came back."""
    kernel.control.cancel(run_id)
    returned = time.time()
    thread.join(timeout=tolerances.JOIN_WAIT_S + clock.stop_bound)
    assert not thread.is_alive(), "conductor never returned"
    return returned


def _setsid_descendant(tree: support.Tree) -> ancestry.ProcInfo:
    wrapper_group = next(iter(tree.wrapper)).pgid
    (own_session,) = [p for p in tree.descendants if p.pgid != wrapper_group]
    return own_session


@pytest.mark.proves(
    "WR-CANCEL-1",
    "WR-CANCEL-1:own-session-descendant-attributable",
    "core",
    "core",
    "PROC",
    "BOTH",
)
@pytest.mark.proves(
    "WR-CANCEL-5", "WR-CANCEL-5:stub-subprocess-killed", "core", "core", "PROC", "BOTH"
)
def test_cancel_stops_attributable_tree_within_bound(short_stop: None) -> None:
    """Survey E4/E5 replay: the plugin ignores ctx.cancelled and spawns a same-group grandchild, a
    setsid'd child whose parent is alive, and sits in a readiness-poll loop."""
    kernel = support.spine_kernel()
    order = support.admit_order(
        kernel, "tree", {"seconds": tolerances.JOIN_WAIT_S * 6, "hold_term": True}
    )
    run_dir = support.run_dir_of(kernel, order.run_id)
    thread = support.drive_in_thread(kernel, order)
    with support.reaping(order.run_id):
        tree = support.observe_tree(kernel, order.run_id)
        (wrapper,) = tree.wrapper
        (child,) = tree.child
        # the child keeps the wrapper's group: the recorded group covers the tree (DD-1(b))
        assert child.pgid == wrapper.pgid
        own_session = _setsid_descendant(tree)
        assert own_session.pgid != wrapper.pgid and own_session.ppid == child.pid
        term_log = run_dir / "work" / "tmp" / "term_at"
        assert not term_log.exists()

        died: list[float] = []

        def watch() -> None:
            while support.alive_marked({own_session}, order.run_id):
                time.sleep(tolerances.POLL_FINE_S)
            died.append(time.time())

        sampler = threading.Thread(target=watch, daemon=True)
        sampler.start()

        requested = time.time()
        returned = _cancel_and_join(kernel, order.run_id, thread)
        sampler.join(timeout=tolerances.JOIN_WAIT_S)

        assert support.kinds(run_dir)[-1] == "cancelled"
        # ancestry is empty by terminal + stop_bound + tolerance
        assert support.wait_until(
            lambda: not support.alive_marked(tree.live, order.run_id),
            clock.stop_bound + tolerances.PROC_WAIT_S,
        )
        assert returned - requested <= clock.stop_bound + tolerances.PROC_WAIT_S
        # the setsid'd child got SIGTERM first (it ignores it, and logs it), and died only after
        # `grace` had passed: SIGKILL, not the SIGTERM, ended it
        term_at = float(term_log.read_text(encoding="utf-8"))
        assert requested <= term_at
        assert died, "the setsid'd child never went away"
        assert died[0] - term_at >= clock.grace - tolerances.POLL_S
        assert died[0] - term_at <= clock.grace + clock.kill + tolerances.POLL_S


@pytest.mark.proves(
    "WR-CANCEL-1", "WR-CANCEL-1:cancel-during-blocking-wait", "core", "core", "PROC", "BOTH"
)
def test_cancel_during_blocking_wait_ends_tree_and_wakes_caller(short_stop: None) -> None:
    """The plugin sits in a readiness wait that never consults ctx.cancelled and the caller is
    itself blocked in `run(wait_ms=...)`: a cancel from another call ends the tree and the caller
    is answered with the terminal view well inside its own wait."""
    kernel = support.spine_kernel()
    wait_ms = int(tolerances.JOIN_WAIT_S * 1000 * 6)
    answers: list[Any] = []

    def caller() -> None:
        answers.append(
            kernel.control.run(
                plugin="tree", args={"seconds": tolerances.JOIN_WAIT_S * 6}, wait_ms=wait_ms
            )
        )

    blocked = threading.Thread(target=caller, daemon=True)
    started = time.monotonic()
    blocked.start()
    run_dir = None
    assert support.wait_until(
        lambda: bool(sorted((kernel.home / "runs").glob("*/r_*"))), tolerances.JOIN_WAIT_S
    )
    run_dir = sorted((kernel.home / "runs").glob("*/r_*"))[0]
    run_id = run_dir.name
    with support.reaping(run_id):
        support.wait_ready(run_dir)
        live = support.marked(run_id)
        assert live
        kernel.control.cancel(run_id)
        blocked.join(timeout=tolerances.JOIN_WAIT_S + clock.stop_bound)
        assert not blocked.is_alive(), "the blocked caller was never woken"
        assert time.monotonic() - started < wait_ms / 1000 / 2  # far short of its own wait
        assert answers[0].state == "cancelled"
        assert support.wait_until(
            lambda: not support.alive_marked(live, run_id),
            clock.stop_bound + tolerances.PROC_WAIT_S,
        )


@pytest.mark.proves(
    "WR-CANCEL-1",
    "WR-CANCEL-1:own-session-descendant-attributable",
    "core",
    "core",
    "PROC",
    "BOTH",
)
def test_orphaned_own_session_descendant_still_stopped(short_stop: None) -> None:
    """A process in its own session whose parent has already exited (reparented to init while the
    run is live) is attributable through the row made while its parent lived, and is stopped."""
    kernel = support.spine_kernel()
    order = support.admit_order(
        kernel, "tree", {"seconds": tolerances.JOIN_WAIT_S * 6, "orphan_via": True}
    )
    run_dir = support.run_dir_of(kernel, order.run_id)
    thread = support.drive_in_thread(kernel, order)
    with support.reaping(order.run_id):
        support.wait_ready(run_dir)
        wrapper_group = next(
            iter(support.Tree(order.run_id, support.marked(order.run_id)).wrapper)
        ).pgid
        # the setsid'd sleeper: marker-bearing, its own group, parent the intermediate
        sleeper = next(
            p
            for p in support.marked(order.run_id)
            if "time.sleep" in p.argv and p.pgid != wrapper_group and p.pgid == p.pid
        )
        assert support.wait_until(
            lambda: any(
                int(r["pid"]) == sleeper.pid for r in support.rows_of(run_dir, "process_identity")
            ),
            tolerances.JOIN_WAIT_S,
        )
        (run_dir / "work" / "tmp" / "release_mid").write_text("1", encoding="utf-8")
        assert support.wait_until(
            lambda: next((p.ppid for p in ancestry.snapshot() if p.pid == sleeper.pid), None) == 1,
            tolerances.JOIN_WAIT_S,
        ), "the sleeper was never reparented to init"
        _cancel_and_join(kernel, order.run_id, thread)
        assert support.wait_until(
            lambda: not support.alive_marked({sleeper}, order.run_id),
            clock.stop_bound + tolerances.PROC_WAIT_S,
        )


def test_wrapper_has_no_timer(tmp_path: Path) -> None:
    """T-1 is gone: the wrapper keeps no clock and kills nothing on its own. A run whose deadline is
    far shorter than its plugin is left alone by a bare wrapper (only the supervisor enforces the
    deadline, B2-C10)."""
    assert "timeout_s" not in inspect.signature(reactor.run_reactor).parameters
    src = Path(reactor.__file__).read_text(encoding="utf-8")
    assert "monotonic" not in src and ".kill(" not in src
    assert "timeout_s" not in Path(wrapper_main.__file__ or "").read_text(encoding="utf-8")

    from tests.proof import harness

    kernel = support.spine_kernel()
    plugin_seconds = support.SHORT_DEADLINE_S + tolerances.SETTLE_LONG_S * 2
    with harness.patch_snapshot(kernel, "slow", timeout_s=support.SHORT_DEADLINE_S):
        order = support.admit_order(kernel, "slow", {"seconds": plugin_seconds})
    run_dir = support.run_dir_of(kernel, order.run_id)
    spec = json.loads((run_dir / "evidence" / "spec.json").read_text(encoding="utf-8"))
    assert spec["timeout_s"] < plugin_seconds  # the deadline is shorter than the plugin
    env = {**os.environ, "TRESTLE_HOME": str(kernel.home), "PYTHONPATH": str(REPO)}
    started = time.monotonic()
    wrapper = subprocess.run(
        [sys.executable, "-m", "trestle.wrapper.main", "--run-dir", str(run_dir)],
        env=env,
        capture_output=True,
        text=True,
        timeout=tolerances.JOIN_WAIT_S * 3,
        start_new_session=True,
    )
    assert time.monotonic() - started >= spec["timeout_s"]
    assert wrapper.returncode == 0, wrapper.stderr
    report = json.loads((run_dir / "evidence" / "wrapper_report.json").read_text(encoding="utf-8"))
    assert report["classification"] == "succeeded" and report["exit_code"] == 0


def test_sigterm_flushes_a_bounded_console_and_report_without_waiting_on_pipes(
    tmp_path: Path,
) -> None:
    """The wrapper's SIGTERM handler writes what was captured and exits, though a descendant that
    outlived the plugin still holds the console pipes open (a read to EOF would never return)."""
    kernel = support.spine_kernel()
    order = support.admit_order(kernel, "pipe_holder", {"seconds": tolerances.JOIN_WAIT_S * 6})
    run_dir = support.run_dir_of(kernel, order.run_id)
    env = {**os.environ, "TRESTLE_HOME": str(kernel.home), "PYTHONPATH": str(REPO)}
    wrapper = subprocess.Popen(
        [sys.executable, "-m", "trestle.wrapper.main", "--run-dir", str(run_dir)],
        env=env,
        start_new_session=True,
    )
    with support.reaping(order.run_id):
        try:
            assert support.wait_until(
                lambda: (run_dir / "work" / "tmp" / "ready").exists(), tolerances.JOIN_WAIT_S
            )
            time.sleep(tolerances.SETTLE_SHORT_S)
            assert wrapper.poll() is None, "the wrapper ended without waiting on the held pipes"
            sent = time.monotonic()
            wrapper.send_signal(signal.SIGTERM)
            assert wrapper.wait(timeout=tolerances.PROC_WAIT_S) == 128 + signal.SIGTERM
            assert time.monotonic() - sent < tolerances.PROC_WAIT_S
            console = (run_dir / "evidence" / "console" / "stdout.log").read_text(encoding="utf-8")
            assert "hello from the plugin" in console
            report = json.loads(
                (run_dir / "evidence" / "wrapper_report.json").read_text(encoding="utf-8")
            )
            # the report describes the child, which had already returned; the keys are unchanged
            assert set(report) == {"exit_code", "classification", "limits_exceeded"}
            assert report["exit_code"] == 0
        finally:
            if wrapper.poll() is None:
                wrapper.kill()
                wrapper.wait(timeout=tolerances.PROC_WAIT_S)


# -- the stopper against a planted process table and signaller ---------------------------------

SIGTERM, SIGKILL = signal.SIGTERM, signal.SIGKILL
START = support.START
FakeHost = support.FakeHost


def _stop(host: FakeHost, attr: Attribution) -> procident.GroupStop:
    return procident.stop_group(
        attr,
        signaller=host,
        grace=tolerances.SETTLE_SHORT_S,
        kill=tolerances.SETTLE_SHORT_S,
        poll=tolerances.POLL_FINE_S,
    )


def _tree(host: FakeHost) -> Attribution:
    """leader 100 (group 100), member 101, a setsid'd child 102 of 101 that ignores SIGTERM, its
    own child 103 (group 102), and an unrelated process 200 with a child 201."""
    host.add(100, 1, 100)
    host.add(101, 100, 100)
    host.add(102, 101, 102, ignore=(SIGTERM,))
    host.add(103, 102, 102)
    host.add(200, 1, 200)
    host.add(201, 200, 200)
    attr = host.attribution(100)
    assert attr.attribute_leader(100) is not None
    return attr


def test_stopper_term_first_kill_after_grace_and_row_before_every_signal() -> None:
    host = FakeHost()
    attr = _tree(host)
    began = time.monotonic()
    stop = _stop(host, attr)

    assert stop == procident.GroupStop(confirmed_gone=True, signalled=True)
    assert stop.method == "signal"
    terms = [s for s in host.sent if s[2] == SIGTERM]
    kills = [s for s in host.sent if s[2] == SIGKILL]
    # SIGTERM to the recorded group and to each attributable process outside it, then SIGKILL
    assert ("group", 100) in {(k, t) for k, t, *_ in terms}
    assert {t for k, t, *_ in terms if k == "pid"} == {102, 103}
    assert {t for k, t, *_ in kills if k == "pid"} == {102}
    # no sleep before the first signal, and SIGKILL only after `grace` had run out
    assert terms[0][3] - began < tolerances.SETTLE_SHORT_S
    assert kills[0][3] - terms[0][3] >= tolerances.SETTLE_SHORT_S
    # one row per attributed process, each written before any signal was sent
    assert sorted(i.pid for i in host.recorded) == [100, 101, 102, 103]
    assert all({100, 101, 102, 103} <= rowed for rowed in host.rowed_at_signal)
    # nothing unrelated was ever touched
    assert {t for k, t, *_ in host.sent if k == "pid"}.isdisjoint({200, 201})
    assert ("group", 200) not in {(k, t) for k, t, *_ in host.sent}
    assert set(host.rows) == {200, 201}


def test_stopper_never_signals_a_reused_pid() -> None:
    host = FakeHost()
    attr = _tree(host)
    # the recorded setsid'd child dies and an unrelated process takes its pid (another start)
    del host.rows[102]
    host.add(102, 1, 102, start=START + 1)
    host.add(300, 102, 102, start=START + 2)  # and has a child of its own
    stop = _stop(host, attr)
    assert stop.confirmed_gone  # the recorded identity is reused, so it counts as gone
    assert 102 not in {t for k, t, *_ in host.sent if k == "pid"}
    assert 300 not in {t for k, t, *_ in host.sent if k == "pid"}
    assert ("group", 102) not in {(k, t) for k, t, *_ in host.sent}
    assert {102, 300} <= set(host.rows)  # still alive: never touched


def test_stopper_unconfirmed_when_a_process_survives_sigkill() -> None:
    host = FakeHost()
    attr = _tree(host)
    host.ignores[102] = {SIGTERM, SIGKILL}
    stop = _stop(host, attr)
    assert stop == procident.GroupStop(confirmed_gone=False, signalled=True)
    assert 102 in host.rows


def test_stopper_signals_nothing_when_nothing_attributable_is_alive() -> None:
    host = FakeHost()
    attr = _tree(host)
    for pid in (100, 101, 102, 103):
        del host.rows[pid]
    stop = _stop(host, attr)
    assert stop == procident.GroupStop(confirmed_gone=True, signalled=False)
    assert stop.method == "exit"
    assert host.sent == []


def test_an_emptied_group_does_not_attribute_a_stranger_reusing_its_pgid() -> None:
    host = FakeHost()
    attr = _tree(host)
    assert attr.observe()  # 100..103 attributed, group live
    for pid in (100, 101, 102, 103):
        del host.rows[pid]
    assert attr.observe() == {}  # the group emptied
    host.add(400, 1, 100, start=START + 9)  # a stranger now carries the old group id
    assert attr.observe() == {}
    assert 400 not in {i.pid for i in host.recorded}
    assert _stop(host, attr).signalled is False
    assert host.sent == []


def test_a_second_stop_after_close_touches_nothing() -> None:
    host = FakeHost()
    attr = _tree(host)
    first = _stop(host, attr)
    attr.close()
    host.add(500, 1, 100, start=START + 5)
    sent_before = len(host.sent)
    assert _stop(host, attr) == first
    assert len(host.sent) == sent_before


def test_child_shares_the_wrapper_group_in_a_live_run() -> None:
    kernel = support.spine_kernel()
    order = support.admit_order(kernel, "tree", {"seconds": tolerances.JOIN_WAIT_S * 6})
    run_dir = support.run_dir_of(kernel, order.run_id)
    thread = support.drive_in_thread(kernel, order)
    with support.reaping(order.run_id):
        tree = support.observe_tree(kernel, order.run_id)
        (wrapper,) = tree.wrapper
        (child,) = tree.child
        assert child.pgid == wrapper.pgid == wrapper.pid
        assert support.wait_until(
            lambda: any(
                int(r["pid"]) == child.pid for r in support.rows_of(run_dir, "process_identity")
            ),
            tolerances.JOIN_WAIT_S,
        )
        row = next(
            r for r in support.rows_of(run_dir, "process_identity") if int(r["pid"]) == child.pid
        )
        assert row["group"] == wrapper.pid and row["leader"] is False
        kernel.control.cancel(order.run_id)
        thread.join(timeout=tolerances.JOIN_WAIT_S + clock.stop_bound)
