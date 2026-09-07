"""Spawn child process for one run."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def spawn_child(run_dir: Path) -> subprocess.Popen[str]:
    cmd = [sys.executable, "-m", "trestle.child.main", "--run-dir", str(run_dir)]
    env = os.environ.copy()
    root = str(Path(__file__).resolve().parents[2])
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = root if not existing else f"{root}{os.pathsep}{existing}"
    return subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        start_new_session=True,
    )
