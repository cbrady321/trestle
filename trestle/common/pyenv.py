"""The one import environment for every process that runs plugin code (WR-PLAN-6).

The publication validator, the conductor's wrapper and the wrapper's child all start from
`build_child_env` and `python_argv`, so a plugin imports the same modules in each and the
validator's verdict is the run's. Nothing is added to the import path: no repo root, no
packs directory, and, through `python -P`, no working directory. What a plugin can import is
what the interpreter's own environment provides (installed distributions, plus whatever
PYTHONPATH the operator started the server with, passed through unchanged).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def build_child_env(*, home: Path | None = None) -> dict[str, str]:
    """The environment a plugin-code process starts with: the server's, unchanged, with
    TRESTLE_HOME set to `home` when one is given."""
    env = os.environ.copy()
    if home is not None:
        env["TRESTLE_HOME"] = str(home)
    return env


def python_argv(*args: str) -> list[str]:
    """`python -P <args>`: the interpreter that runs this process, without the current
    working directory (or the script directory) put on `sys.path`."""
    return [sys.executable, "-P", *args]
