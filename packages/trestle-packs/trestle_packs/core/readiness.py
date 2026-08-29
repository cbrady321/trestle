"""Readiness polling helpers."""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class PollConfig:
    timeout_s: float = 120.0
    interval_s: float = 2.0


def poll_until(
    check: Callable[[], bool],
    *,
    config: PollConfig | None = None,
    on_retry: Callable[[], None] | None = None,
) -> None:
    """Block until ``check()`` returns True or timeout."""
    cfg = config or PollConfig()
    deadline = time.monotonic() + cfg.timeout_s
    while time.monotonic() < deadline:
        if check():
            return
        if on_retry is not None:
            on_retry()
        time.sleep(cfg.interval_s)
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
