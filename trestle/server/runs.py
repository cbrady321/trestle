"""Active run registry — process tracking and cancel signals."""

from __future__ import annotations

import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from trestle.common import clock
from trestle.common.fsutil import atomic_write
from trestle.server.ledger import work_dir
from trestle.server.procident import Attribution

# The S0 names of the two stop bounds, read through from their one definition in clock.py (SA-05):
# tests/proof/tolerances.py falls back to them only while clock.py does not define the bound.
CANCEL_GRACE_S = clock.grace
CANCEL_KILL_S = clock.kill


def cancel_flag_path(run_dir: Path) -> Path:
    return work_dir(run_dir) / "cancel.flag"


def release_point_flag_path(run_dir: Path) -> Path:
    """The flag U2 writes itself at the run's release point (B2-C10): the child's cancel signal
    reads it (with the cancel flag) so a plan can release cooperatively before the kill."""
    return work_dir(run_dir) / "release_point.flag"


@dataclass
class RunRegistry:
    _active: dict[str, tuple[subprocess.Popen[str], Attribution]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    # told after a cancel flag is written (the conductor finalizes a run still waiting in the
    # queue, B2-C12); it signals nothing
    on_cancel_flag: Callable[[str], None] | None = None

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
        """The request path's whole part in a cancel: write the flag (B2-C10 "Only signaller").
        Signalling and killing belong to the conductor (U2), which sees the flag, records the stop
        and stops the run's processes."""
        atomic_write(cancel_flag_path(run_dir), b"1")
        if self.on_cancel_flag is not None:
            self.on_cancel_flag(run_id)
