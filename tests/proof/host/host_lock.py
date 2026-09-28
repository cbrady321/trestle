"""CSC-12: the HOST lock. `hold()` serializes every HOST runner
(`proc_gate run`, `docker_gate` modes) around the one clean venv
re-point, so concurrent HOST runs never race each other's `pip install`.
"""

from __future__ import annotations

import contextlib
import fcntl
import os
import subprocess
import time
from pathlib import Path

LOCK_PATH = Path("/Users/colinbrady/projects/trestle-wt/.host.lock")
HELD_ENV = "TRESTLE_HOST_LOCK_HELD"


class HostRunTimedOut(RuntimeError):
    """A HOST run exceeded its bound (`HOST_RUN_MAX`); its process group
    was killed and the lock freed."""


def _repoint_venv(worktree: Path, pip_runner=None) -> None:
    """`pip install --no-deps -e . -e packages/trestle-packs` (plus
    `-e packages/trestle-env` once that directory exists), run from
    `worktree`, under the lock. The project only, never a tool."""
    runner = pip_runner or (
        lambda args, cwd: subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    )
    args = ["pip", "install", "--no-deps", "-e", "."]
    packs = worktree / "packages" / "trestle-packs"
    if packs.exists():
        args += ["-e", str(packs)]
    env_pkg = worktree / "packages" / "trestle-env"
    if env_pkg.exists():
        args += ["-e", str(env_pkg)]
    runner(args, worktree)


@contextlib.contextmanager
def hold(
    worktree: Path | None = None,
    lock_path: Path | None = None,
    pip_runner=None,
    repoint: bool = True,
    host_run_max: float | None = None,
    clock=None,
    kill_process_group=None,
):
    """Take the exclusive HOST lock (Python `fcntl.flock`; this host has
    no `flock(1)`), re-point the clean venv at `worktree` under it, and
    set `TRESTLE_HOST_LOCK_HELD=1` for this process and its children —
    only when that variable is unset. When it is already set, an outer
    caller holds the lock: `hold()` takes nothing, so nested acquisition
    never deadlocks."""
    lock_path = lock_path or LOCK_PATH
    already_held = os.environ.get(HELD_ENV) == "1"

    if already_held:
        yield
        return

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(lock_path, "a+")
    start = (clock or time.time)()
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        os.environ[HELD_ENV] = "1"
        if repoint and worktree is not None:
            _repoint_venv(worktree, pip_runner=pip_runner)
        if host_run_max is not None and (clock or time.time)() - start > host_run_max:
            if kill_process_group:
                kill_process_group()
            raise HostRunTimedOut("HOST run timed out")
        yield
    finally:
        os.environ.pop(HELD_ENV, None)
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        fh.close()
