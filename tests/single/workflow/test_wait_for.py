"""V-2 `CancelSignal.wait_for` (DEFERRED-DECISIONS section 1): a unit's one wait on a state.

It returns CONDITION when the condition holds (before the first wait or mid-wait), INTERRUPTED
promptly on a stop flag or the root goal's flip from another thread, and TIMED_OUT at the bound
after one last read. `cause()` stays flag-only."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

from tests.single.workflow import loopkit as kit
from trestle.child.run_services import CANCEL_FLAG, FlagCancelSignal
from trestle.workflow import loop
from trestle.workflow.values import Goal, StopCause, WaitOutcome

LATENCY_S = loop.WATCH_INTERVAL_S * 4
LONG = timedelta(seconds=60)


def _rig_signal() -> tuple[loop._NodeSignal, kit.ManualClock, list[Goal]]:
    clock = kit.ManualClock()
    goal = [Goal.CONVERGE]
    return loop._NodeSignal(kit.RigCancel(clock), lambda: goal[0]), clock, goal


def test_condition_before_the_first_wait() -> None:
    signal, clock, _ = _rig_signal()
    assert signal.wait_for(lambda: True, LONG) is WaitOutcome.CONDITION
    assert clock.now == kit.NOW  # nothing was waited


def test_condition_mid_wait() -> None:
    signal, clock, _ = _rig_signal()
    due = kit.NOW + timedelta(seconds=3)
    assert signal.wait_for(lambda: clock.now >= due, LONG) is WaitOutcome.CONDITION
    assert due <= clock.now < due + timedelta(seconds=1)


def test_timed_out_at_the_bound_after_a_last_read() -> None:
    signal, clock, _ = _rig_signal()
    assert signal.wait_for(lambda: False, timedelta(seconds=2)) is WaitOutcome.TIMED_OUT
    # the bound's last read sees the state the final wait reached
    held = kit.NOW + timedelta(seconds=4)
    assert signal.wait_for(lambda: clock.now >= held, timedelta(seconds=2)) is (
        WaitOutcome.CONDITION
    )


def test_interrupted_by_the_goal_on_a_manual_clock() -> None:
    signal, clock, goal = _rig_signal()
    flip_at = kit.NOW + timedelta(seconds=5)

    def flips() -> bool:
        if clock.now >= flip_at:
            goal[0] = Goal.RELEASE  # a sibling raised
        return False

    assert signal.wait_for(flips, LONG) is WaitOutcome.INTERRUPTED
    assert flip_at <= clock.now < flip_at + timedelta(seconds=1)
    assert signal.cause() is None


def _flag_signal(tmp_path: Path, goal: list[Goal]) -> loop._NodeSignal:
    root = FlagCancelSignal(tmp_path, poll=loop.WATCH_INTERVAL_S)
    return loop._NodeSignal(root, lambda: goal[0])


def _interrupt_latency(signal: loop._NodeSignal, trigger: Callable[[], object]) -> float:
    """Start a wait, fire `trigger` from another thread once the wait is in progress, and return
    the seconds from the trigger to the wait's return."""
    started = threading.Event()
    fired: list[float] = []

    def fire() -> None:
        started.wait()
        fired.append(time.monotonic())
        trigger()

    thread = threading.Thread(target=fire)
    thread.start()

    def condition() -> bool:
        started.set()
        return False

    outcome = signal.wait_for(condition, LONG)
    returned = time.monotonic()
    thread.join()
    assert outcome is WaitOutcome.INTERRUPTED
    return returned - fired[0]


def test_interrupted_promptly_by_the_cancel_flag(tmp_path: Path) -> None:
    goal = [Goal.CONVERGE]
    signal = _flag_signal(tmp_path, goal)
    latency = _interrupt_latency(signal, lambda: (tmp_path / CANCEL_FLAG).write_text("1"))
    assert latency < LATENCY_S
    assert signal.cause() is StopCause.CANCEL


def test_interrupted_promptly_by_the_goal_flip(tmp_path: Path) -> None:
    goal = [Goal.CONVERGE]
    signal = _flag_signal(tmp_path, goal)
    latency = _interrupt_latency(signal, lambda: goal.__setitem__(0, Goal.RELEASE))
    assert latency < LATENCY_S
    assert signal.cause() is None  # B2-C15: the flip is not a stop cause
