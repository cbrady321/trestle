"""Spawn child process for one run."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def spawn_child(run_dir: Path) -> subprocess.Popen[str]:
    """Start the run's child in the wrapper's own process group, so the group the supervisor
    records covers the tree (design DD-1(b), B2-C10)."""
    cmd = [sys.executable, "-m", "trestle.child.main", "--run-dir", str(run_dir)]
    env = os.environ.copy()
    root = str(Path(__file__).resolve().parents[2])
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = root if not existing else f"{root}{os.pathsep}{existing}"
    return subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
