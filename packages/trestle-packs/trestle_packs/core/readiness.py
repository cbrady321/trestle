"""Readiness polling helpers."""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class PollConfig:
    timeout_s: float = 120.0
    interval_s: float = 2.0


class PollCancelled(Exception):
    """Raised by ``poll_until`` when its cancellation token is set (BFD-45)."""


class CancelToken(Protocol):
    """A cancellation token: ``threading.Event`` satisfies it."""

    def is_set(self) -> bool: ...


def poll_until(
    check: Callable[[], bool],
    *,
    config: PollConfig | None = None,
    on_retry: Callable[[], None] | None = None,
    clock: Callable[[], float] | None = None,
    sleep: Callable[[float], None] | None = None,
    cancel: CancelToken | None = None,
) -> None:
    """Block until ``check()`` returns True or timeout.

    ``clock`` and ``sleep`` default to ``time.monotonic`` and ``time.sleep``. A set ``cancel``
    token raises ``PollCancelled`` before the next ``check()`` call; a token with a ``wait``
    method (``threading.Event``) also wakes the default sleep, so a cancel is seen at once
    rather than after the interval.
    """
    cfg = config or PollConfig()
    now = clock or time.monotonic
    nap = sleep
    if nap is None:
        wait = getattr(cancel, "wait", None)
        nap = wait if callable(wait) else time.sleep
    deadline = now() + cfg.timeout_s
    while now() < deadline:
        if cancel is not None and cancel.is_set():
            raise PollCancelled("readiness poll cancelled")
        if check():
            return
        if on_retry is not None:
            on_retry()
        nap(cfg.interval_s)
    if cancel is not None and cancel.is_set():
        raise PollCancelled("readiness poll cancelled")
    msg = f"readiness check timed out after {cfg.timeout_s}s"
    raise TimeoutError(msg)


def run_probe(command: list[str], *, timeout_s: float = 10.0) -> bool:
    """Return True when a probe command exits 0."""
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0
