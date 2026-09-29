"""Spawn child process for one run."""

from __future__ import annotations

import subprocess
from pathlib import Path

from trestle.common.pyenv import build_child_env, python_argv


def spawn_child(run_dir: Path) -> subprocess.Popen[str]:
    """Start the run's child in the wrapper's own process group, so the group the supervisor
    records covers the tree (design DD-1(b), B2-C10)."""
    return subprocess.Popen(
        python_argv("-m", "trestle.child.main", "--run-dir", str(run_dir)),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=build_child_env(),
    )
