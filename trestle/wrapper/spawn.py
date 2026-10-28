"""Spawn child process for one run."""

from __future__ import annotations

import subprocess
from pathlib import Path

from trestle.common import credentials
from trestle.common.pyenv import build_child_env, python_argv


def spawn_child(
    run_dir: Path, delivered: credentials.Delivered | None = None
) -> subprocess.Popen[str]:
    """Start the run's child in the wrapper's own process group, so the group the supervisor
    records covers the tree (design DD-1(b), B2-C10). The run's credential values go to it over
    a pipe of its own (`credentials.Handover`), never in its environment or argv."""
    env = build_child_env()
    handover = credentials.Handover(delivered or {})
    pass_fds = handover.prepare(env)
    try:
        proc = subprocess.Popen(
            python_argv("-m", "trestle.child.main", "--run-dir", str(run_dir)),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            pass_fds=pass_fds,
        )
    except BaseException:
        handover.failed()
        raise
    handover.started()
    return proc
