"""BFD-45 (L.NW-1.4): `poll_until` gains a keyword-only clock, sleep and cancellation token; the
defaults keep today's sleep behaviour exactly (WR-DEADLINE-4:legacy-poll-until-cancel-and-clock).

The first test pins today's default behaviour and was green before the change."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

import pytest
from tests.proof import tolerances

from trestle_packs.core import readiness
from trestle_packs.core.readiness import PollConfig, poll_until

TIMEOUT_TEXT = "readiness check timed out after {}s"


class _FakeTime:
    """A clock and a sleep that share one virtual timeline (no real waiting)."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def test_poll_until_default_sleep_behaviour_pinned(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeTime()
    monkeypatch.setattr(readiness.time, "monotonic", fake.monotonic)
    monkeypatch.setattr(readiness.time, "sleep", fake.sleep)
    calls: list[float] = []
    retries: list[float] = []

    def check() -> bool:
        calls.append(fake.now)
        return len(calls) == 3

    poll_until(
        check,
        config=PollConfig(timeout_s=10.0, interval_s=2.0),
        on_retry=lambda: retries.append(fake.now),
    )
    # a check, an on_retry, then time.sleep(interval_s), until the check passes
    assert calls == [0.0, 2.0, 4.0]
    assert retries == [0.0, 2.0]
    assert fake.sleeps == [2.0, 2.0]

    fake2 = _FakeTime()
    monkeypatch.setattr(readiness.time, "monotonic", fake2.monotonic)
    monkeypatch.setattr(readiness.time, "sleep", fake2.sleep)
    with pytest.raises(TimeoutError) as excinfo:
        poll_until(lambda: False, config=PollConfig(timeout_s=5.0, interval_s=2.0))
    assert str(excinfo.value) == TIMEOUT_TEXT.format(5.0)
    assert fake2.sleeps == [2.0, 2.0, 2.0]


def test_poll_until_injected_clock_times_out_without_sleep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def real_sleep_forbidden(_seconds: float) -> None:
        raise AssertionError("real sleep used")

    monkeypatch.setattr(readiness.time, "sleep", real_sleep_forbidden)
    monkeypatch.setattr(readiness.time, "monotonic", real_sleep_forbidden)
    fake = _FakeTime()
    with pytest.raises(TimeoutError) as excinfo:
        poll_until(
            lambda: False,
            config=PollConfig(timeout_s=30.0, interval_s=7.0),
            clock=fake.monotonic,
            sleep=fake.sleep,
        )
    assert str(excinfo.value) == TIMEOUT_TEXT.format(30.0)
    assert fake.sleeps == [7.0] * 5  # 35 s of virtual time, none of it real


def test_poll_until_cancel_returns_within_bound() -> None:
    bound = tolerances.stop_bound()

    # (1) cancelled while the poll sleeps: PollCancelled, and check() is never called again
    fake = _FakeTime()
    cancel = threading.Event()
    checks: list[float] = []

    def cancelling_sleep(seconds: float) -> None:
        fake.sleep(seconds)
        cancel.set()

    def check() -> bool:
        checks.append(fake.now)
        return False

    with pytest.raises(readiness.PollCancelled):
        poll_until(
            check,
            config=PollConfig(timeout_s=bound * 100, interval_s=2.0),
            clock=fake.monotonic,
            sleep=cancelling_sleep,
            cancel=cancel,
        )
    assert checks == [0.0]  # zero check() calls after the cancel
    assert fake.now <= bound

    # (2) an already-set token is honoured before the first check
    with pytest.raises(readiness.PollCancelled):
        poll_until(lambda: pytest.fail("check() after cancel"), cancel=cancel)

    # (3) real time: a cancel during the default sleep is seen within the MC-09 stop bound, far
    # inside the poll interval
    real_cancel = threading.Event()
    timer = threading.Timer(tolerances.SETTLE_SHORT_S, real_cancel.set)
    started = time.monotonic()
    timer.start()
    try:
        with pytest.raises(readiness.PollCancelled):
            poll_until(
                lambda: False,
                config=PollConfig(timeout_s=bound * 100, interval_s=bound * 10),
                cancel=real_cancel,
            )
    finally:
        timer.cancel()
    assert time.monotonic() - started <= bound


def test_defaults_and_signature_keep_existing_callers() -> None:
    import inspect

    params = inspect.signature(poll_until).parameters
    assert list(params)[:1] == ["check"]
    for name in ("config", "on_retry", "clock", "sleep", "cancel"):
        assert params[name].kind is inspect.Parameter.KEYWORD_ONLY, name
        assert params[name].default is None, name
    assert PollConfig() == PollConfig(timeout_s=120.0, interval_s=2.0)
    ok: Callable[[], bool] = lambda: True  # noqa: E731
    assert poll_until(ok) is None
