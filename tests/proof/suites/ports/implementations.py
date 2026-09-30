"""The implementations registered against the families (B3-C17): `[fake-command]`, `[fake-marker]`.

Each entry maps an implementation id to `(family, factory)`. A factory builds a FRESH
implementation each call (the suite runs each case on its own) under a directory it takes from
`tmp_path`. The real adapters register here in their own leaves (`real-command`, ...) and run the
same family, unmodified.
"""

from __future__ import annotations

import itertools
from collections.abc import Callable
from pathlib import Path

from trestle_packs.fakes import FakeCommand, FakeMarker, TestCounts, failed_result, passed_result

from tests.proof.suites.ports import core, families
from tests.proof.suites.ports.families import COMMAND_EXECUTION, LOCAL_PROCESS_SUPERVISION
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


IMPLEMENTATIONS: dict[str, tuple[str, Factory]] = {
    "fake-command": (COMMAND_EXECUTION, fake_command),
    "fake-marker": (LOCAL_PROCESS_SUPERVISION, fake_marker),
    # the same family against a second implementation: a file-backed marker with no RUN form
    "fake-marker-durable": (
        LOCAL_PROCESS_SUPERVISION,
        lambda base: fake_marker(base, lifetime="durable", name="fake-marker-durable"),
    ),
}

_ = families  # importing the module registers the families
