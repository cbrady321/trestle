"""Active run registry — process tracking and cancel signals."""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from trestle.common.fsutil import atomic_write
from trestle.server.ledger import work_dir

CANCEL_GRACE_S = float(os.environ.get("TRESTLE_CANCEL_GRACE_S", "10"))
CANCEL_KILL_S = float(os.environ.get("TRESTLE_CANCEL_KILL_S", "5"))


def cancel_flag_path(run_dir: Path) -> Path:
    return work_dir(run_dir) / "cancel.flag"


def terminate_process_group(proc: subprocess.Popen[str], *, grace_s: float, kill_s: float) -> None:
    if proc.poll() is not None:
        return
    try:
        pgid = os.getpgid(proc.pid)
    except OSError:
        proc.terminate()
        proc.wait(timeout=kill_s)
        return

    if grace_s > 0:
        time.sleep(grace_s)
    if proc.poll() is None:
        try:
            os.killpg(pgid, signal.SIGTERM)
        except OSError:
            proc.terminate()

    deadline = time.monotonic() + kill_s
    while proc.poll() is None and time.monotonic() < deadline:
        time.sleep(0.05)
    if proc.poll() is None:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except OSError:
            proc.kill()
        proc.wait()


@dataclass
class RunRegistry:
    _active: dict[str, subprocess.Popen[str]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def register(self, run_id: str, proc: subprocess.Popen[str]) -> None:
        with self._lock:
            self._active[run_id] = proc

    def unregister(self, run_id: str) -> None:
        with self._lock:
            self._active.pop(run_id, None)

    def is_active(self, run_id: str) -> bool:
        with self._lock:
            proc = self._active.get(run_id)
            return proc is not None and proc.poll() is None

    def request_cancel(self, run_id: str, run_dir: Path) -> None:
        atomic_write(cancel_flag_path(run_dir), b"1")
        with self._lock:
            proc = self._active.get(run_id)
        if proc is not None and proc.poll() is None:
            terminate_process_group(proc, grace_s=CANCEL_GRACE_S, kill_s=CANCEL_KILL_S)
