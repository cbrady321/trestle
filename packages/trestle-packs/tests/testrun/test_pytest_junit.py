"""L.RB-5.1: the pytest-selector runner over the real `CommandPort` and real pytest children
(PROC, venue BOTH; OQ-23): counts include setup, teardown and collection errors, failing ids come
back, a missing or disagreeing artifact is `CONTRACT_VIOLATION`, and the full output stays behind a
handle. These read the runner's own results; the family cases (`test_conformance.py`) run the same
facts through the one suite on the fake and the real runner.
"""

from __future__ import annotations

import ast
import hashlib
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from tests.proof.suites.ports.families import FUTURE, Cancel, effect_call, ticket
from trestle.workflow import ports
from trestle.workflow.declarations import EffectFacetClass
from trestle.workflow.values import Confirmation, ConfirmationStatus

from trestle_packs.process.command import CommandPort
from trestle_packs.testrun import PytestJunitRunner, read_junit
from trestle_packs.testrun import pytest_junit as runner_module

from . import rig


def run(runner: PytestJunitRunner, command: ports.BoundCommand, effect: str) -> tuple[Any, Any]:
    t = ticket(effect, EffectFacetClass.EVENT)
    confirmation, result = runner.run(command, t, Cancel(), FUTURE)
    assert confirmation.status is ConfirmationStatus.APPLIED
    assert result is not None
    return result, t


@pytest.fixture
def real(tmp_path: Path) -> tuple[PytestJunitRunner, dict[str, ports.BoundCommand]]:
    return (
        PytestJunitRunner(CommandPort(), tmp_path / "artifacts"),
        rig.real_tasks(tmp_path / "project"),
    )


@pytest.mark.proves("WR-VERIFY-4", "WR-VERIFY-4:runner-counts-errors", "B", "B", "PROC", "BOTH")
def test_counts_include_setup_teardown_collection_errors(
    real: tuple[PytestJunitRunner, dict[str, ports.BoundCommand]],
) -> None:
    runner, tasks = real
    seen = {}
    for task in ("setup_error", "teardown_error", "collection_error"):
        result, _ = run(runner, tasks[task], task)
        assert ports.ExecutionClass(result.classification) is ports.ExecutionClass.FAILED
        seen[task] = result.counts
    # one error each, and the tests that ran fine beside a broken fixture still count as passed
    assert (seen["setup_error"].passed, seen["setup_error"].errors) == (1, 1)
    assert (seen["teardown_error"].passed, seen["teardown_error"].errors) == (1, 1)
    assert (seen["collection_error"].passed, seen["collection_error"].errors) == (0, 1)
    assert all(c.failed == 0 for c in seen.values())


def test_failing_ids_returned(
    real: tuple[PytestJunitRunner, dict[str, ports.BoundCommand]],
) -> None:
    runner, tasks = real
    result, _ = run(runner, tasks["failing"], "failing")
    assert ports.ExecutionClass(result.classification) is ports.ExecutionClass.FAILED
    assert result.failing == ("test_failing::test_bad_one", "test_failing::test_bad_two")
    assert (result.counts.passed, result.counts.failed) == (1, 2)
    errored, _ = run(runner, tasks["setup_error"], "setup_error")
    assert errored.failing == ("test_setup_error::test_needs_broken_fixture",)
    clean, _ = run(runner, tasks["passing"], "passing")
    assert clean.failing == ()


def test_missing_counts_is_contract_violation(
    real: tuple[PytestJunitRunner, dict[str, ports.BoundCommand]],
) -> None:
    runner, tasks = real
    lies, _ = run(runner, tasks["console_lies"], "lies")  # prints "5 passed", writes no report
    assert ports.ExecutionClass(lies.classification) is ports.ExecutionClass.CONTRACT_VIOLATION
    assert lies.counts is None and lies.recorded.passed is False
    assert "5 passed" in lies.excerpt  # console text is evidence, never the result
    split, _ = run(runner, tasks["exit_disagrees"], "disagrees")
    assert ports.ExecutionClass(split.classification) is ports.ExecutionClass.CONTRACT_VIOLATION


def test_no_tests_collected_is_contract_violation(tmp_path: Path) -> None:
    """pytest exits 5 over an empty selection: a report with no failure and a non-zero status."""
    project = tmp_path / "project"
    tasks = rig.real_tasks(project)
    (project / "test_nothing.py").write_text("x = 1\n", encoding="utf-8")
    command = replace(
        tasks["passing"], argv=(*tasks["passing"].argv[:-1], str(project / "test_nothing.py"))
    )
    result, _ = run(PytestJunitRunner(CommandPort(), tmp_path / "artifacts"), command, "empty")
    assert result.exit_status == 5
    assert ports.ExecutionClass(result.classification) is ports.ExecutionClass.CONTRACT_VIOLATION


@pytest.mark.proves("B9.2", "B9.2", "B", "B", "PROC", "BOTH")
def test_full_output_behind_handle(
    real: tuple[PytestJunitRunner, dict[str, ports.BoundCommand]],
) -> None:
    runner, tasks = real
    result, t = run(runner, tasks["chatty"], "chatty")
    handle = runner.output(t)
    assert handle is not None
    data = Path(handle.path).read_bytes()
    assert data == handle.read()
    assert handle.size == len(data) > 512
    assert handle.sha256 == hashlib.sha256(data).hexdigest()
    assert data.startswith(b"trestle-testrun-suite-output-begins\noutput line 0\n")
    assert len(result.excerpt.encode()) <= 512
    assert data.decode().endswith(result.excerpt)
    assert runner.output(ticket("never-ran", EffectFacetClass.EVENT)) is None


def test_the_artifact_is_named_by_ticket_and_replaced_by_the_next_attempt(
    real: tuple[PytestJunitRunner, dict[str, ports.BoundCommand]],
) -> None:
    runner, tasks = real
    _, first = run(runner, tasks["passing"], "up")
    again = ticket("up", EffectFacetClass.EVENT, attempt=2)
    runner.run(tasks["failing"], again, Cancel(), FUTURE)
    one, two = runner.output(first), runner.output(again)
    assert one is not None and two is not None
    assert one.path != two.path and Path(one.path).parent.name == "up.1"
    assert Path(two.path).parent.name == "up.2"
    assert (Path(one.path).parent / runner_module.JUNIT_NAME).exists()


class Recording:
    """An `ExecutionPort` that only records what it is asked to run."""

    def __init__(self, inner: CommandPort | None = None) -> None:
        self.commands: list[ports.BoundCommand] = []
        self._inner = inner

    def policy(self, command: Any) -> Any:
        return CommandPort().policy(command)

    def release_descriptor(self, call: Any) -> Any:
        return CommandPort().release_descriptor(call)

    def run(self, command: Any, t: Any, cancel: Any, until: Any) -> Any:
        self.commands.append(command)
        if self._inner is not None:
            return self._inner.run(command, t, cancel, until)
        return Confirmation(
            ConfirmationStatus.NOT_APPLIED, "execution.toolchain_missing", None
        ), None


def test_the_wrapped_port_runs_exactly_one_launcher_command(tmp_path: Path) -> None:
    recording = Recording()
    runner = PytestJunitRunner(recording, tmp_path / "artifacts")
    command = rig.real_tasks(tmp_path / "project")["passing"]
    t = ticket("up", EffectFacetClass.EVENT)
    confirmation, result = runner.run(command, t, Cancel(), FUTURE)
    assert confirmation.status is ConfirmationStatus.NOT_APPLIED and result is None  # unchanged
    assert runner.output(t) is None  # nothing started, no handle
    (launched,) = recording.commands
    directory = tmp_path / "artifacts" / "r_suite_0001" / "suite" / "up.1"
    exe = command.resolved.executable
    assert launched.argv[:4] == (exe, "-c", runner_module._LAUNCH, str(directory / "output.log"))
    assert launched.argv[4 : 4 + len(command.argv)] == command.argv  # the selector, argv[0] first
    assert launched.argv[-1] == f"--junitxml={directory / 'junit.xml'}"
    assert launched.reports_tests is False and launched.resolved == command.resolved
    assert launched.environment == command.environment


def test_a_command_that_reports_no_tests_is_passed_through(tmp_path: Path) -> None:
    recording = Recording()
    runner = PytestJunitRunner(recording, tmp_path / "artifacts")
    command = replace(rig.real_tasks(tmp_path / "project")["passing"], reports_tests=False)
    runner.run(command, ticket("up", EffectFacetClass.EVENT), Cancel(), FUTURE)
    assert recording.commands == [command]


def test_argv0_must_be_the_resolved_executable(tmp_path: Path) -> None:
    runner = PytestJunitRunner(Recording(), tmp_path / "artifacts")
    command = rig.real_tasks(tmp_path / "project")["passing"]
    with pytest.raises(ValueError, match="argv"):
        runner.run(
            replace(command, argv=("pytest", *command.argv[1:])),
            ticket("up", EffectFacetClass.EVENT),
            Cancel(),
            FUTURE,
        )


def test_the_descriptor_and_policy_are_the_wrapped_ports(tmp_path: Path) -> None:
    runner = PytestJunitRunner(CommandPort(), tmp_path / "artifacts")
    command = rig.real_tasks(tmp_path / "project")["passing"]
    assert runner.policy(command) == CommandPort().policy(command)
    call = effect_call("run", {"command": command}, effect="x")
    assert ports.as_descriptor(runner.release_descriptor(call)) == ports.InRunGroup(False)


def test_read_junit_sums_suites_and_is_none_when_unreadable(tmp_path: Path) -> None:
    good = tmp_path / "a.xml"
    good.write_text(
        '<testsuites><testsuite tests="4" failures="1" errors="1" skipped="1">'
        '<testcase classname="m" name="a"><failure/></testcase>'
        '<testcase classname="" name="b"><error/></testcase></testsuite>'
        '<testsuite tests="2" failures="0" errors="0" skipped="0"/></testsuites>',
        encoding="utf-8",
    )
    counts, failing = read_junit(good) or (None, None)
    assert counts is not None and (counts.passed, counts.failed, counts.errors) == (3, 1, 1)
    assert counts.skipped == 1 and failing == ("m::a", "b")
    torn = tmp_path / "b.xml"
    torn.write_text("<testsuite tests=", encoding="utf-8")
    empty = tmp_path / "c.xml"
    empty.write_text("<testsuites/>", encoding="utf-8")
    assert read_junit(torn) is None and read_junit(empty) is None
    assert read_junit(tmp_path / "absent.xml") is None


def test_the_runner_imports_only_stdlib_and_trestle_workflow() -> None:
    """A packs subpackage may import the standard library, `trestle.workflow` and itself (N7)."""
    base = Path(runner_module.__file__).parent
    offenders = []
    for path in sorted(base.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = (
                [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else [node.module or ""]
                if isinstance(node, ast.ImportFrom) and node.level == 0
                else []
            )
            for name in names:
                top = name.split(".")[0]
                if top in sys.stdlib_module_names or top == "__future__":
                    continue
                if not name.startswith(("trestle.workflow", "trestle_packs.testrun")):
                    offenders.append(f"{path.name}: {name}")
    assert offenders == []
