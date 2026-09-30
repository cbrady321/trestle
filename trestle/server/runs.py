"""Active run registry — process tracking and cancel signals."""

from __future__ import annotations

import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path

from trestle.common import clock
from trestle.common.fsutil import atomic_write
from trestle.server.ledger import work_dir
from trestle.server.procident import Attribution, stop_group

# The S0 names of the two stop bounds, read through from their one definition in clock.py (SA-05):
# tests/proof/tolerances.py falls back to them only while clock.py does not define the bound.
CANCEL_GRACE_S = clock.grace
CANCEL_KILL_S = clock.kill


def cancel_flag_path(run_dir: Path) -> Path:
    return work_dir(run_dir) / "cancel.flag"


@dataclass
class RunRegistry:
    _active: dict[str, tuple[subprocess.Popen[str], Attribution]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def register(self, run_id: str, proc: subprocess.Popen[str], attribution: Attribution) -> None:
        with self._lock:
            self._active[run_id] = (proc, attribution)

    def unregister(self, run_id: str) -> None:
        with self._lock:
            self._active.pop(run_id, None)

    def is_active(self, run_id: str) -> bool:
        with self._lock:
            entry = self._active.get(run_id)
            return entry is not None and entry[0].poll() is None

    def request_cancel(self, run_id: str, run_dir: Path) -> None:
        atomic_write(cancel_flag_path(run_dir), b"1")
        with self._lock:
            entry = self._active.get(run_id)
        if entry is not None and entry[0].poll() is None:
            stop_group(entry[1])
