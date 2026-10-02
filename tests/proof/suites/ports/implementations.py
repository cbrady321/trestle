"""The implementations registered against the families (B3-C17): `[fake-command]`, `[fake-marker]`.

Each entry maps an implementation id to `(family, factory)`. A factory builds a FRESH
implementation each call (the suite runs each case on its own) under a directory it takes from
`tmp_path`. The real adapters register here in their own leaves (`real-command`, ...) and run the
same family, unmodified.
"""

from __future__ import annotations

import itertools
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from trestle_packs.fakes import (
    FakeCommand,
    FakeLocalProcess,
    FakeMarker,
    TestCounts,
    failed_result,
    passed_result,
)
from trestle_packs.process import identity
from trestle_packs.process.command import REPORT_ENV, CommandPort
from trestle_packs.process.local import LocalProcessPort, found_selector

from tests.proof.suites.ports import core, families
from tests.proof.suites.ports.families import COMMAND_EXECUTION, LOCAL_PROCESS_SUPERVISION
from trestle.common import clock
from trestle.workflow import ports
from trestle.workflow.declarations import RealizationKind

type Factory = Callable[[Path], core.Implementation]

_RESOLVED = ports.Resolved("/suite/bin/tool", "1.0", "pin", "adoption")


def _command(task: str, *, reports_tests: bool = False) -> ports.BoundCommand:
    return ports.BoundCommand(
        task, (_RESOLVED.executable, task), {}, _RESOLVED, reports_tests=reports_tests
    )


def fake_command(base: Path, command_class: type[FakeCommand] = FakeCommand) -> core.Implementation:
    results = {
        "pass": passed_result(),
        "fail": failed_result(),
        "tests": passed_result(TestCounts(passed=3, failed=0, errors=0, skipped=1)),
        "tests_no_counts": passed_result(),  # a test selector that reports no counts
        "long": passed_result(),
    }
    commands = {
        "pass": _command("pass"),
        "fail": _command("fail"),
        "tests": _command("tests", reports_tests=True),
        "tests_no_counts": _command("tests_no_counts", reports_tests=True),
        "long": _command("long"),
    }
    return core.Implementation(
        command_class(results), name="fake-command", extras={"commands": commands}
    )


# The real command port runs real children: the suite's five tasks are small Python programs run
# by this interpreter (an absolute path, so `argv[0] == resolved.executable`, B3-C14).
_REPORT = (
    "import os;"
    f"open(os.environ[{REPORT_ENV!r}], 'w').write("
    '\'<testsuite tests="4" failures="0" errors="0" skipped="1">'
    '<testcase classname="suite" name="a"/></testsuite>\')'
)
# `long` is a command that would run on: it is ended by a cancel or a passed deadline, and the suite
# also runs it with neither (`none_result_only_not_applied`), where it ends by itself a few polls
# after it starts. The bound is the supervisor's poll interval times ten (no timing literal).
LONG_RUN_S = clock.poll_interval * 10
_PROGRAMS = {
    "pass": "pass",
    "fail": "raise SystemExit(1)",
    "tests": _REPORT,
    "tests_no_counts": "pass",  # a test selector that writes no report
    "long": f"import time; time.sleep({LONG_RUN_S})",  # outlives a cancel or a passed deadline
}


def real_command(base: Path, command_class: type[CommandPort] = CommandPort) -> core.Implementation:
    resolved = ports.Resolved(sys.executable, "3.12", "pin", "adoption")
    commands = {
        task: ports.BoundCommand(
            task,
            (sys.executable, "-c", program),
            {},
            resolved,
            reports_tests=task in ("tests", "tests_no_counts"),
        )
        for task, program in _PROGRAMS.items()
    }
    return core.Implementation(command_class(), name="real-command", extras={"commands": commands})


_ids = itertools.count()


def fake_marker(
    base: Path,
    marker_class: type[FakeMarker] = FakeMarker,
    lifetime: str = "run",
    name: str = "fake-marker",
) -> core.Implementation:
    root = base / f"markers-{next(_ids)}"
    marker = marker_class(root, lifetime=lifetime)
    specs = {
        "spec": ports.ResourceSpec(
            "suite-db", RealizationKind.AGENT_LAUNCHED_PROJECT, "suite-entry", None
        ),
        "provisioned_spec": ports.ResourceSpec(
            "suite-record", RealizationKind.PROVISIONED, "suite-entry", None
        ),
    }
    return core.Implementation(
        marker,
        core.Reach(fs_roots=(root,), engine_inventory=marker.inventory),
        name=name,
        extras={
            **specs,
            "lifetimes": ("run", "durable") if lifetime == "run" else ("durable",),
            "plant_found": marker.plant_found,
        },
        close=marker.close,
    )


# The local-process family: fake and real answer to one spec, whose bound command is a process that
# lives until it is ended. The comment in its argument text keeps it distinct from any other holder
# on the host (a found instance is a process running the same command line).
_HOLDER = "import signal; signal.pause()  # trestle proof suite: local process holder"
_LISTEN_PORT = "20321"


def _local_spec(tag: str = "") -> ports.ResourceSpec:
    """`tag` (default none) ends the holder's command line: a suite run in parallel with another
    (`xdist`, or another suite over the same holder) passes a tag of its own, so the other's
    processes are not this one's found instances."""
    resolved = ports.Resolved(sys.executable, "3.12", "pin", "adoption")
    holder = f"{_HOLDER} {tag}" if tag else _HOLDER
    command = ports.BoundCommand(
        "app", (sys.executable, "-c", holder), {"PORT": _LISTEN_PORT}, resolved, False
    )
    return ports.ResourceSpec(
        "suite-proc", RealizationKind.AGENT_LAUNCHED_PROJECT, "suite-entry", command
    )


def fake_local(
    base: Path, local_class: type[FakeLocalProcess] = FakeLocalProcess
) -> core.Implementation:
    spec = _local_spec()
    fake = local_class()
    assert spec.command is not None
    argv = spec.command.argv
    return core.Implementation(
        fake,
        core.Reach(engine_inventory=fake.inventory),
        name="fake-local",
        extras={
            "spec": spec,
            "lifetimes": ("run",),
            "plant_found": lambda system: fake.plant_found(system, argv),
        },
        close=fake.close,
    )


def real_local(
    base: Path, local_class: type[LocalProcessPort] = LocalProcessPort, tag: str = ""
) -> core.Implementation:
    spec = _local_spec(tag)
    assert spec.command is not None
    port = local_class()
    planted: list[subprocess.Popen[bytes]] = []

    def plant_found(system: str) -> str:
        # a process that runs the spec's command line and is not this port's: a found instance
        helper = subprocess.Popen(  # noqa: S603 - the suite's own fixed program
            spec.command.argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL
        )
        planted.append(helper)
        start = identity.start_time(helper.pid)
        assert start is not None
        return found_selector(helper.pid, start)

    def close() -> None:
        port.close()
        for helper in planted:
            helper.kill()
            helper.wait()

    return core.Implementation(
        port,
        core.Reach(engine_inventory=port.inventory),
        name="real-local",
        extras={"spec": spec, "lifetimes": ("run",), "plant_found": plant_found},
        close=close,
    )


IMPLEMENTATIONS: dict[str, tuple[str, Factory]] = {
    "fake-command": (COMMAND_EXECUTION, fake_command),
    "real-command": (COMMAND_EXECUTION, real_command),
    "fake-marker": (LOCAL_PROCESS_SUPERVISION, fake_marker),
    "fake-local": (LOCAL_PROCESS_SUPERVISION, fake_local),
    "real-local": (LOCAL_PROCESS_SUPERVISION, real_local),
    # the same family against a second implementation: a file-backed marker with no RUN form
    "fake-marker-durable": (
        LOCAL_PROCESS_SUPERVISION,
        lambda base: fake_marker(base, lifetime="durable", name="fake-marker-durable"),
    ),
}

_ = families  # importing the module registers the families
