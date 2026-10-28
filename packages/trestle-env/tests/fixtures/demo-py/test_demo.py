"""The demo Python project's own tests (L.RB-4.6): what an allowlisted `pytest` task runs.

Not collected by any outer session (the env conftest ignores `fixtures/*`); its own `pytest.ini`
roots the run here. When `DEMO_PY_PROBE` names a file the tests record the interpreter and process
identity they ran under, so the caller can compare it with what it bound."""

from __future__ import annotations

import json
import os
import sys


def test_the_task_ran_on_an_interpreter_the_toolchain_named() -> None:
    assert os.path.isabs(sys.executable)
    assert sys.version_info[:2] >= (3, 12)


def test_the_task_records_the_process_it_ran_as() -> None:
    probe = os.environ.get("DEMO_PY_PROBE")
    if not probe:
        return
    identity = {
        "executable": sys.executable,
        "version": ".".join(map(str, sys.version_info[:3])),
        "pid": os.getpid(),
        "ppid": os.getppid(),
        "pgid": os.getpgrp(),
        "stdin_is_tty": os.isatty(0),
    }
    with open(probe, "w", encoding="utf-8") as handle:
        json.dump(identity, handle)
