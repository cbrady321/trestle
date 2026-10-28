"""CSC-12: the HOST lock. `hold()` serializes every HOST runner
(`proc_gate run`, `docker_gate` modes) around the one clean venv
re-point, so concurrent HOST runs never race each other's `pip install`.
"""

from __future__ import annotations

import contextlib
import fcntl
import os
import subprocess
import sys
import time
from pathlib import Path

LOCK_PATH = Path("/Users/colinbrady/projects/trestle-wt/.host.lock")
HELD_ENV = "TRESTLE_HOST_LOCK_HELD"

# plan-gap (CSC-12 / L.P0-0a.1, DM-45): the plan names the clean venv
# (built from the framework python3.12) but no leaf fixes its path; the
# default is the Mac-only worktree-parent location, like LOCK_PATH.
# `TRESTLE_HOST_VENV` overrides it (selftests, other hosts). Every HOST
# run re-points and runs in this venv, never the ambient interpreter.
HOST_VENV = Path("/Users/colinbrady/projects/trestle-wt/.venv312")
VENV_ENV = "TRESTLE_HOST_VENV"


class HostRunTimedOut(RuntimeError):
    """A HOST run exceeded its bound (`HOST_RUN_MAX`); its process group
    was killed and the lock freed."""


class VenvUnavailable(RuntimeError):
    """The clean HOST venv is missing; the run records PRECONDITION_UNMET
    (MC-27) and never falls back to another interpreter."""


class RepointFailed(RuntimeError):
    """The venv re-point failed; the run must not proceed on a stale venv."""


def venv_path(venv: Path | None = None) -> Path:
    if venv is not None:
        return Path(venv)
    return Path(os.environ.get(VENV_ENV) or HOST_VENV)


def venv_python(venv: Path | None = None) -> Path:
    return venv_path(venv) / "bin" / "python"


def _translated() -> bool:
    """True when this process runs under Rosetta on an arm64 Mac (an x86_64
    launcher such as miniforge's python3.12): its children would run the
    universal2 venv interpreter as x86_64 too, against arm64-only wheels."""
    if sys.platform != "darwin":
        return False
    try:
        out = subprocess.run(
            ["/usr/sbin/sysctl", "-n", "sysctl.proc_translated"],
            capture_output=True,
            text=True,
        ).stdout
    except OSError:
        return False
    return out.strip() == "1"


def venv_command(venv: Path | None = None) -> list[str]:
    """argv prefix that runs the clean venv's python natively (arm64 on an
    arm64 Mac, whatever architecture launched the gate)."""
    prefix = ["/usr/bin/arch", "-arm64"] if _translated() else []
    return [*prefix, str(venv_python(venv))]


def venv_interpreter_info(venv: Path | None = None, runner=None) -> tuple[str, str]:
    """(python version, sys.platform) as reported by the venv's own
    interpreter; ("unavailable", "unavailable") when it cannot be run."""
    py = venv_python(venv)
    code = "import platform, sys; print(platform.python_version()); print(sys.platform)"
    try:
        if runner is not None:
            out = runner([*venv_command(venv), "-c", code]).stdout
        else:
            if not py.exists():
                return ("unavailable", "unavailable")
            out = subprocess.run(
                [*venv_command(venv), "-c", code], capture_output=True, text=True, check=True
            ).stdout
        version, plat = out.split()[:2]
        return (version, plat)
    except (OSError, ValueError, subprocess.SubprocessError):
        return ("unavailable", "unavailable")


def _repoint_venv(worktree: Path, pip_runner=None, venv: Path | None = None) -> None:
    """`<venv>/bin/python -m pip install --no-deps -e . -e
    packages/trestle-packs` (plus `-e packages/trestle-env` once that
    directory exists), run from `worktree`, under the lock. The clean
    venv's pip only, never the ambient one; a failure raises."""
    py = venv_python(venv)
    if pip_runner is None and not py.exists():
        raise VenvUnavailable(f"clean HOST venv missing: {py}")
    runner = pip_runner or (
        lambda args, cwd: subprocess.run(args, cwd=cwd, capture_output=True, text=True)
    )
    args = [*venv_command(venv), "-m", "pip", "install", "--no-deps", "-e", "."]
    packs = worktree / "packages" / "trestle-packs"
    if packs.exists():
        args += ["-e", str(packs)]
    env_pkg = worktree / "packages" / "trestle-env"
    if env_pkg.exists():
        args += ["-e", str(env_pkg)]
    result = runner(args, worktree)
    rc = getattr(result, "returncode", 0)
    if rc:
        tail = (getattr(result, "stderr", "") or "")[-300:]
        raise RepointFailed(f"venv re-point failed (exit {rc}): {tail}")


@contextlib.contextmanager
def hold(
    worktree: Path | None = None,
    lock_path: Path | None = None,
    pip_runner=None,
    venv: Path | None = None,
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
            _repoint_venv(worktree, pip_runner=pip_runner, venv=venv)
        if host_run_max is not None and (clock or time.time)() - start > host_run_max:
            if kill_process_group:
                kill_process_group()
            raise HostRunTimedOut("HOST run timed out")
        yield
    finally:
        os.environ.pop(HELD_ENV, None)
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        fh.close()
