"""The F-E4-1 straddle: an S0-shaped run and a live S0-argv orphan (L.P0-1A.6).

Nothing here scans argv for the orphan, sweeps for it or signals it during a
node (CSC-14, K-19): it is found by the pid of the `Popen` this module made,
observed through the V-2.3 ancestry snapshot, and killed only at teardown,
under `ancestry.reap`, because this module started it.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from tests.pins.a_lifecycle import fossil_producers as fp
from tests.proof import ancestry, tolerances

_HOLD = "import sys, time; time.sleep(float(sys.argv[1]))"
# S0's conductor started each run's wrapper as
# `python -m trestle.wrapper.main --run-dir <run_dir>` in a new session
# (trestle/server/conductor.py); this is the argv marker an S0 run leaves.
S0_ARGV_MARKER = ("trestle.wrapper.main", "--run-dir")

STRADDLE_HOME = fp.FOSSILS_ROOT / fp.BAND / "straddle" / "home"


def straddle_run_dir(tmp_path: Path) -> Path:
    """A scratch copy of the committed straddle fossil (recovery writes into
    the run directory it is given; the committed corpus stays byte-stable)."""
    home = tmp_path / "home"
    shutil.copytree(STRADDLE_HOME, home)
    dirs = sorted((home / "runs").glob("*/r_*"))
    assert len(dirs) == 1, dirs
    return dirs[0]


@contextmanager
def spawn_s0_shaped_orphan(run_dir: Path) -> Iterator[ancestry.ProcInfo]:
    """Spawn, at test time, a live detached process carrying the S0 argv
    marker for `run_dir`; yield its ancestry record; reap it on exit."""
    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            _HOLD,
            str(tolerances.JOIN_WAIT_S * 6),
            *S0_ARGV_MARKER,
            str(run_dir),
        ],
        start_new_session=True,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    info: ancestry.ProcInfo | None = None
    try:
        for _ in range(int(tolerances.JOIN_WAIT_S / tolerances.POLL_S)):
            info = next((p for p in ancestry.snapshot() if p.pid == proc.pid), None)
            if info is not None and info.start is not None:
                break
            time.sleep(tolerances.POLL_S)
        assert info is not None
        yield info
    finally:
        if info is not None:
            ancestry.reap({info})
        else:
            proc.kill()
        proc.wait(timeout=tolerances.PROC_WAIT_S)


def is_alive(info: ancestry.ProcInfo) -> bool:
    """Is the process `info` still the same live process (pid and start)?"""
    return any(p.pid == info.pid and p.start == info.start for p in ancestry.snapshot())
