"""L.RB-5.1: the test-run family's conformance suite, run UNMODIFIED against the stdlib fake and the
real `PytestJunitRunner` (WR-PROOF-4:b-testrun-suite, SA-14). One suite file
(`tests/conformance/testrun_cases.py`), registered through `register_family`, run by `run_family`.
The real binding drives the host's own Python interpreter as a real child, so it is PROC, venue
BOTH: CI runs it here and `host-proc` runs it on the macOS host (OQ-23). Planted defects prove the
suite is not vacuous.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from conformance import testrun_cases
from tests.proof.suites.ports import core

from trestle_packs.fakes.command import ExecutionClass, ExecutionResult, TestCounts
from trestle_packs.fakes.testrun import FakeTestRun
from trestle_packs.testrun import PytestJunitRunner

from . import rig

BINDINGS = [
    pytest.param("fake", id="fake"),
    pytest.param(
        "pytest-junit",
        id="pytest-junit",
        marks=[
            pytest.mark.proves(
                "WR-PROOF-4", "WR-PROOF-4:b-testrun-suite", "B", "B", "PROC", "BOTH"
            ),
        ],
    ),
]


def factory(binding: str, base: Path) -> Callable[[], core.Implementation]:
    if binding == "fake":
        return rig.fake_testrun()
    return rig.real_pytest_junit(base)


@pytest.mark.parametrize("binding", BINDINGS)
def test_testrun_suite(binding: str, tmp_path: Path) -> None:
    run = core.run_family(testrun_cases.FAMILY, factory(binding, tmp_path))
    assert run.cases_run == tuple(c.name for c in testrun_cases.CASES)
    assert run.suite_sha256 == core.sha256_of(Path(testrun_cases.__file__))


# planted defects: each fake / runner has one, and the suite (unmodified) names the case that
# catches it


class _DropsErrors(FakeTestRun):
    def run(self, command: Any, ticket: Any, cancel: Any, until: Any) -> Any:
        confirmation, result = super().run(command, ticket, cancel, until)
        if result is not None and result.counts is not None and result.counts.errors:
            counts = TestCounts(result.counts.passed, result.counts.failed, 0, 0)
            result = ExecutionResult(
                result.exit_status, ExecutionClass.FAILED, counts, result.failing, None, ""
            )
        return confirmation, result


class _NoFailingIds(FakeTestRun):
    def run(self, command: Any, ticket: Any, cancel: Any, until: Any) -> Any:
        confirmation, result = super().run(command, ticket, cancel, until)
        if result is not None:
            result = ExecutionResult(
                result.exit_status,
                result.classification,
                result.counts,
                (),
                result.code,
                result.excerpt,
            )
        return confirmation, result


class _MissingCountsPasses(FakeTestRun):
    def run(self, command: Any, ticket: Any, cancel: Any, until: Any) -> Any:
        confirmation, result = super().run(command, ticket, cancel, until)
        if result is not None and result.counts is None:
            result = ExecutionResult(0, ExecutionClass.PASSED, None, (), None, result.excerpt)
        return confirmation, result


class _NoOutputHandle(FakeTestRun):
    def output(self, ticket: Any) -> None:
        return None


class _OutputIsTheExcerpt(FakeTestRun):
    """Keeps only the excerpt as "the full output"."""

    def run(self, command: Any, ticket: Any, cancel: Any, until: Any) -> Any:
        confirmation, result = super().run(command, ticket, cancel, until)
        if result is not None:
            self._outputs[command.task] = result.excerpt
        return confirmation, result


class _InterruptedWithoutCode(FakeTestRun):
    def run(self, command: Any, ticket: Any, cancel: Any, until: Any) -> Any:
        confirmation, result = super().run(command, ticket, cancel, until)
        if result is not None and result.classification is ExecutionClass.INTERRUPTED:
            result = ExecutionResult(
                result.exit_status, result.classification, None, (), None, result.excerpt
            )
        return confirmation, result


@pytest.mark.parametrize(
    ("defect", "caught_by"),
    [
        (_DropsErrors, "counts_include_setup_teardown_and_collection_errors"),
        (_NoFailingIds, "failing_ids_are_returned"),
        (_MissingCountsPasses, "missing_counts_is_a_contract_violation"),
        (_NoOutputHandle, "full_output_stays_behind_a_handle"),
        (_InterruptedWithoutCode, "cancel_and_until_yield_interrupted_with_code"),
    ],
)
def test_testrun_suite_catches_planted_defects(defect: type[FakeTestRun], caught_by: str) -> None:
    with pytest.raises(core.SuiteFailure) as caught:
        core.run_family(testrun_cases.FAMILY, rig.fake_testrun(defect))
    assert caught_by in str(caught.value)


def test_a_handle_that_is_only_the_excerpt_is_caught() -> None:
    # `_OutputIsTheExcerpt` overwrites the script after the first run: the second run's handle
    # (or the first's) no longer holds the whole output
    with pytest.raises(core.SuiteFailure) as caught:
        core.run_family(testrun_cases.FAMILY, rig.fake_testrun(_OutputIsTheExcerpt))
    assert "full_output_stays_behind_a_handle" in str(caught.value)


class _ExitOnlyRunner(PytestJunitRunner):
    """A runner that decides from the exit status alone, reading no artifact."""

    def _classified(self, status: int, report: Any, excerpt: str) -> ExecutionResult:
        klass = ExecutionClass.PASSED if status == 0 else ExecutionClass.FAILED
        return ExecutionResult(status, klass, TestCounts(0, 0, 0, 0), (), None, excerpt)  # type: ignore[arg-type]


def test_a_runner_that_ignores_the_artifact_is_caught(tmp_path: Path) -> None:
    tasks = rig.real_tasks(tmp_path / "project")

    def build() -> core.Implementation:
        from trestle_packs.process.command import CommandPort

        runner = _ExitOnlyRunner(CommandPort(), tmp_path / "artifacts")
        return core.Implementation(runner, name="exit-only", extras={"tasks": tasks})

    with pytest.raises(core.SuiteFailure) as caught:
        core.run_family(testrun_cases.FAMILY, build)
    message = str(caught.value)
    assert "counts_include_setup_teardown_and_collection_errors" in message
    assert "missing_counts_is_a_contract_violation" in message
