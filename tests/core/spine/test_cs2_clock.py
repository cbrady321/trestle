"""CS-2 one clock (L.CS-2.3; B2-C5, B2-C10): the deadline fixed at admission is the one enforced,
so queue time counts; the class comes from the first cause observed, cancel first if both hold;
the scheduler's slot is released on every exit; the kill runs on every terminal path."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from tests.core.spine import support
from tests.proof import ancestry, harness, tolerances
from trestle.common import clock
from trestle.server.conductor import Conductor
from trestle.server.runs import cancel_flag_path

LONG_S = tolerances.JOIN_WAIT_S * 6


@pytest.fixture(autouse=True)
def short_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(clock, "grace", support.TEST_GRACE_S)
    monkeypatch.setattr(clock, "kill", support.TEST_KILL_S)


def _wait_terminal(run_dir: Path, thread: Any) -> None:
    thread.join(timeout=support.SHORT_DEADLINE_S + tolerances.JOIN_WAIT_S + clock.stop_bound)
    assert not thread.is_alive(), "conductor never returned"
    assert support.kinds(run_dir)[-1] in {"succeeded", "failed", "cancelled", "timed_out"}


@pytest.mark.proves("A5.1", "A5.1", "A", "core", "PROC", "BOTH")
@pytest.mark.proves(
    "WR-DEADLINE-2", "WR-DEADLINE-2:ends-in-deadline-window", "core", "core", "PROC", "BOTH"
)
@pytest.mark.proves(
    "WR-DEADLINE-2", "WR-DEADLINE-2:no-process-survives", "core", "core", "PROC", "BOTH"
)
@pytest.mark.proves(
    "WR-CANCEL-1", "WR-CANCEL-1:deadline-tree-within-bound", "core", "core", "PROC", "BOTH"
)
def test_short_deadline_stops_tree_in_window() -> None:
    """Deadline D is measured from admission and dispatch is held for a while in the queue:
    the run still ends timed out in [D, D + stop_bound + tolerance], the tree gone and released."""
    kernel = support.spine_kernel()
    # The queue-time assert below has `hold` less the stop's own time as its slack, so the hold
    # is most of a doubled deadline (only a stall of about `hold` could fail it), and the deadline
    # still leaves the dispatched tree room to spawn before it fires.
    deadline_s = support.SHORT_DEADLINE_S * 2
    hold = tolerances.SETTLE_LONG_S * 4
    admitted_after = time.time()  # before admission, so the admitted deadline is >= this + D
    with harness.patch_snapshot(kernel, "tree", timeout_s=deadline_s):
        order = support.admit_order(kernel, "tree", {"seconds": LONG_S})
    time.sleep(hold)  # dispatch held: queue time
    run_dir = support.run_dir_of(kernel, order.run_id)
    thread = support.drive_in_thread(kernel, order)
    with support.reaping(order.run_id):
        _wait_terminal(run_dir, thread)
        ended = time.time()
        assert support.kinds(run_dir)[-1] == "timed_out"
        window = ended - admitted_after
        assert deadline_s <= window <= (deadline_s + clock.stop_bound + tolerances.PROC_WAIT_S), (
            window
        )
        # the queue time counted: a run given its timeout afresh from spawn would end `hold` later
        assert window < deadline_s + hold
        assert not support.marked(order.run_id) or support.wait_until(
            lambda: not support.marked(order.run_id), clock.stop_bound + tolerances.PROC_WAIT_S
        )
        view = kernel.control.project.status(order.run_id)
        assert view.cleanup is not None and view.cleanup.processes == "released"  # type: ignore[union-attr]


@pytest.mark.proves("WR-OWN-3", "WR-OWN-3:non-success-no-survivor", "core", "core", "PROC", "BOTH")
def test_failed_run_survivor_killed() -> None:
    """A raising plugin leaves a process of its own group behind: it is stopped by the terminal row
    plus stop_bound and a tolerance, and the run's group_stop says a signal did it."""
    kernel = support.spine_kernel()
    order = support.admit_order(
        kernel, "leaver", {"seconds": LONG_S, "fail": True, "wait_go": True}
    )
    run_dir = support.run_dir_of(kernel, order.run_id)
    thread = support.drive_in_thread(kernel, order)
    with support.reaping(order.run_id):
        support.wait_ready(run_dir)
        survivor = support.marked(order.run_id) - {
            p for p in support.marked(order.run_id) if "trestle." in p.argv
        }
        assert survivor, "the plugin left nothing behind"
        (run_dir / "work" / "tmp" / "go").write_text("1", encoding="utf-8")  # now fail
        _wait_terminal(run_dir, thread)
        assert support.kinds(run_dir)[-1] == "failed"
        assert support.wait_until(
            lambda: not support.alive_marked(survivor, order.run_id),
            clock.stop_bound + tolerances.PROC_WAIT_S,
        )
        (row,) = support.rows_of(run_dir, "group_stop")
        assert (row["confirmed_gone"], row["method"]) == (True, "signal")


def _first_cause_run(kernel: Any, order: Any, *, cancel_first: bool) -> str:
    run_dir = support.run_dir_of(kernel, order.run_id)
    if cancel_first:
        # both hold when the conductor first looks: cancel is the class (B2-C10)
        cancel_flag_path(run_dir).parent.mkdir(parents=True, exist_ok=True)
        cancel_flag_path(run_dir).write_bytes(b"1")
    thread = support.drive_in_thread(kernel, order)
    _wait_terminal(run_dir, thread)
    return support.kinds(run_dir)[-1]


def test_deadline_vs_cancel_first_cause() -> None:
    # (1) both hold at the first observation: the deadline has passed and the flag is written
    kernel = support.spine_kernel()
    with harness.patch_snapshot(kernel, "slow", timeout_s=support.SHORT_DEADLINE_S):
        order = support.admit_order(kernel, "slow", {"seconds": LONG_S})
    time.sleep(support.SHORT_DEADLINE_S + tolerances.SETTLE_SHORT_S)
    assert _first_cause_run(kernel, order, cancel_first=True) == "cancelled"

    # (2) the deadline is first; a cancel arrives while the deadline's own stop is in its grace:
    # the class stays the first cause, timed_out, and the cancel changes nothing
    kernel = support.spine_kernel()
    with harness.patch_snapshot(kernel, "tree", timeout_s=support.SHORT_DEADLINE_S):
        order = support.admit_order(kernel, "tree", {"seconds": LONG_S, "hold_term": True})
    run_dir = support.run_dir_of(kernel, order.run_id)
    thread = support.drive_in_thread(kernel, order)
    with support.reaping(order.run_id):
        support.wait_ready(run_dir)
        term_log = run_dir / "work" / "tmp" / "term_at"
        # the setsid child ignores SIGTERM: once it logs one, the deadline's stop is in its grace
        assert support.wait_until(
            term_log.exists, support.SHORT_DEADLINE_S + tolerances.JOIN_WAIT_S
        )
        kernel.control.cancel(order.run_id)  # a cancel arrives mid-stop
        _wait_terminal(run_dir, thread)
        assert support.kinds(run_dir)[-1] == "timed_out"

    # (3) a plain cancel long before the deadline
    kernel = support.spine_kernel()
    order = support.admit_order(kernel, "slow", {"seconds": LONG_S})
    run_dir = support.run_dir_of(kernel, order.run_id)
    thread = support.drive_in_thread(kernel, order)
    time.sleep(tolerances.SETTLE_LONG_S)
    kernel.control.cancel(order.run_id)
    _wait_terminal(run_dir, thread)
    assert support.kinds(run_dir)[-1] == "cancelled"


def test_scheduler_released_on_drive_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    kernel = support.spine_kernel()
    conductor = kernel.control.conductor

    def boom(*args: object, **kwargs: object) -> list[str]:
        raise RuntimeError("finalization blew up")

    # (1) an exception after the run ended: the slot is still released, the tree still stopped
    order = support.admit_order(kernel, "leaver", {"seconds": LONG_S, "fail": True})
    assert order.run_id in conductor.scheduler.queue
    run_dir = support.run_dir_of(kernel, order.run_id)
    with support.reaping(order.run_id):
        monkeypatch.setattr(Conductor, "_promote_outputs", boom)
        with pytest.raises(RuntimeError, match="finalization blew up"):
            conductor.drive(order)
        assert order.run_id not in conductor.scheduler.queue
        assert support.wait_until(
            lambda: not support.marked(order.run_id), clock.stop_bound + tolerances.PROC_WAIT_S
        )
        assert support.rows_of(run_dir, "group_stop")
    monkeypatch.undo()

    # (2) an exception before any process exists: the slot is released too
    kernel = support.spine_kernel()
    order = support.admit_order(kernel, "echo", {"message": "x"})
    assert order.run_id in kernel.control.conductor.scheduler.queue
    real = subprocess.Popen

    def no_spawn(*args: Any, **kwargs: Any) -> Any:
        raise OSError("no processes for you")

    monkeypatch.setattr(subprocess, "Popen", no_spawn)
    with pytest.raises(OSError, match="no processes"):
        kernel.control.conductor.drive(order)
    monkeypatch.setattr(subprocess, "Popen", real)
    assert order.run_id not in kernel.control.conductor.scheduler.queue
    assert not ancestry.survivors(set(), set())
