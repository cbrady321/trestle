"""L.RB-4.6 (OQ-23): a Python project proven at HOST. `demo-py`'s allowlisted `pytest` task runs on
the host's real Python interpreter through the `ExecutionPort`, the pin resolved by `stub_mise`
(mode `adopted-interpreter`: the clean gate venv, DM-45) to that interpreter's absolute path, and
the run records its identity (absolute path and the version the interpreter reports).

Resolution stays STUB-PROVEN (the WR-ENV-15 labels); the execution on the real interpreter is a
claim, proven in CI and, through `host-proc`, on the macOS host (PROC, venue BOTH). No Docker, no
install or download: the task needs only the interpreter, and a decoy `python` on the search path
is never invoked.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from trestle.workflow.ports import ExecutionClass, Unresolved

from trestle_packs.process.command import CommandPort, scrubbed_path
from trestle_packs.toolchain import MiseToolchainResolver, reproduction
from trestle_packs.toolchain.tasks import (
    TASK_START,
    ProjectTasks,
    TaskDeclaration,
    TaskRunner,
)

from . import rig
from .test_task_run import TICKET, Events

DEMO_PY = rig.REPO / "packages" / "trestle-env" / "tests" / "fixtures" / "demo-py"
PIN = "3.12"  # the catalog's demo-py pin
PROJECT = "demo-py"
PYTEST_ARGV = ("python", "-m", "pytest", "-q", "./")  # the reference catalog's demo-py `pytest`


class Bench:
    def __init__(self, tmp_path: Path) -> None:
        self.world = rig.World(tmp_path / "world")
        self.world.mode = "adopted-interpreter"
        self.world.write_config()
        self.probe = tmp_path / "probe.json"
        self.decoy_log = tmp_path / "decoy.log"
        self.environment = {"DEMO_PY_PROBE": str(self.probe)}
        mise_env = {"STUB_MISE_CONFIG": str(self.world.config)}
        port = CommandPort()
        self.resolver = MiseToolchainResolver(
            self.world.stub, port, {PROJECT: mise_env}, envelope=self.world.envelope
        )
        self.events = Events()
        project = ProjectTasks(
            str(DEMO_PY),
            {"pytest": TaskDeclaration("pytest", PYTEST_ARGV, reports_tests=True)},
            self.environment,
        )
        self.runner = TaskRunner(self.resolver, port, {PROJECT: project}, evidence=self.events)

    def plant_decoys(self) -> None:
        """A `python` and a `python3` first on the search path that log any invocation."""
        for name in ("python", "python3"):
            rig.write_executable(
                self.world.base / "decoys" / name,
                f"#!/bin/sh\necho {name} >> {self.decoy_log}\nexit 99\n",
            )
        os.environ["PATH"] = str(self.world.base / "decoys") + os.pathsep + os.environ["PATH"]


@pytest.fixture
def bench(tmp_path: Path) -> Any:
    bench = Bench(tmp_path)
    try:
        yield bench
    finally:
        bench.world.restore()


@pytest.mark.proves("WR-ENV-3", "WR-ENV-3:python-host", "B", "B", "PROC", "BOTH")
@pytest.mark.proves("WR-EVID-10", "WR-EVID-10:python-host", "B", "B", "PROC", "BOTH")
def test_demo_py_task_runs_on_real_interpreter_identity_recorded(bench: Bench) -> None:
    bench.plant_decoys()
    command = bench.runner.bind(PROJECT, "pytest")
    assert not isinstance(command, Unresolved)
    interpreter = command.resolved.executable
    assert os.path.isabs(interpreter) and command.argv[0] == interpreter == sys.executable
    confirmation, result = bench.runner.run(PROJECT, "pytest", TICKET)
    assert confirmation.status.value == "applied"
    assert result is not None and result.exit_status == 0  # the task's own pytest run
    assert ExecutionClass(result.classification) is ExecutionClass.PASSED
    assert result.counts is not None and result.counts.passed == 2 and result.counts.failed == 0
    (start,) = (fields for kind, fields in bench.events.items if kind == TASK_START)
    assert start["executable"] == interpreter  # identity: the absolute path ...
    assert str(start["reported_version"]).startswith(PIN)  # ... and the version it reported
    assert command.resolved.reported_version == start["reported_version"]
    identity = json.loads(bench.probe.read_text())
    assert identity["executable"] == interpreter  # the run really was that interpreter
    assert identity["version"] == start["reported_version"]
    # in the run's group, a direct child, stdin closed: attributable to the run (MC-13, V-2.3)
    assert identity["pgid"] == os.getpgrp() and identity["ppid"] == os.getpid()
    assert identity["stdin_is_tty"] is False
    assert not bench.decoy_log.exists(), "a decoy python on PATH was invoked"


@pytest.mark.proves("WR-ENV-3", "WR-ENV-3:python-host", "B", "B", "PROC", "BOTH")
@pytest.mark.proves("WR-EVID-10", "WR-EVID-10:python-host", "B", "B", "PROC", "BOTH")
def test_recorded_argv_reproduces_by_hand(bench: Bench) -> None:
    command = bench.runner.bind(PROJECT, "pytest")
    assert not isinstance(command, Unresolved)
    _, result = bench.runner.run(PROJECT, "pytest", TICKET)
    assert result is not None and result.counts is not None
    environment = {**command.environment, "PATH": scrubbed_path(command.resolved.executable)}
    by_hand = subprocess.run(  # noqa: S603 - the recorded absolute argv is the point
        reproduction(command.argv, environment),
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert by_hand.returncode == result.exit_status == 0
    assert f"{result.counts.passed} passed" in by_hand.stdout
    assert Path(json.loads(bench.probe.read_text())["executable"]) == Path(command.argv[0])


def test_the_pin_is_resolved_without_installing_or_searching(bench: Bench) -> None:
    before = rig.core.tree_state(bench.world.envelope)
    resolved = bench.resolver.resolve(PROJECT, "python")
    assert not isinstance(resolved, Unresolved)
    assert resolved.executable == sys.executable
    assert rig.core.tree_state(bench.world.envelope) == before
