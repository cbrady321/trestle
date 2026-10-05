"""The test-run family's conformance cases (L.RB-5.1; B3-C14, B3-C17, B3-C21, B9.2).

`TestRun` is `ExecutionPort` (B3-C21), so this family is the execution suite of B3-C17 narrowed to
test selectors: errors are counted (setup, teardown and collection errors included), failing ids
are returned, a selector whose result carries no counts (or whose exit status and report disagree)
is `CONTRACT_VIOLATION`, cancel and `until` yield `INTERRUPTED` with its code and never `None`, and
the full console output stays behind a handle while the result carries only a bounded excerpt.

Registered through the suite core's `register_family` as family `testrun` and run, UNMODIFIED, by
`run_family` against the stdlib fake and the real `PytestJunitRunner` (WR-PROOF-4). A case reaches
the implementation only through the port's members, `output(ticket)` (the handle) and the factory's
`Implementation.extras`, its fixture contract:

- `tasks`: a mapping of the suite's test selectors, each a `BoundCommand` with `reports_tests`
  true, by name: `passing`, `failing`, `setup_error`, `teardown_error`, `collection_error`,
  `console_lies` (prints a passing summary and writes no report), `exit_disagrees` (exits non-zero
  over a report with no failure), `chatty` (prints `HEAD`, then far more than an excerpt holds,
  then `TAIL`, and passes) and `long` (runs until cancelled);
- every name in `EXPECTED` has the counts and failing ids the selector's run must report.

No case branches on which implementation it runs against (`test_cases_unbranched`).
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from tests.proof.suites.ports import core
from tests.proof.suites.ports.families import (
    FUTURE,
    PAST,
    Cancel,
    effect_call,
    status,
    ticket,
)
from trestle.workflow import ports
from trestle.workflow.declarations import EffectFacetClass
from trestle.workflow.values import ConfirmationStatus, StopCause

FAMILY = "testrun"
EXECUTION_CANCELLED = "execution.cancelled"
EXECUTION_DEADLINE = "execution.deadline_exceeded"
TEXT_MAX = 512  # V-13: the bound of an excerpt
HEAD = "trestle-testrun-suite-output-begins"
TAIL = "trestle-testrun-suite-output-ends"


@dataclass(frozen=True)
class Expected:
    passed: int
    failed: int
    errors: int
    skipped: int
    failing: tuple[str, ...]


# What each selector's run reports. Failing ids are junit ids: `classname::name`.
EXPECTED: dict[str, Expected] = {
    "passing": Expected(2, 0, 0, 1, ()),
    "failing": Expected(1, 2, 0, 0, ("test_failing::test_bad_one", "test_failing::test_bad_two")),
    "setup_error": Expected(1, 0, 1, 0, ("test_setup_error::test_needs_broken_fixture",)),
    "teardown_error": Expected(1, 0, 1, 0, ("test_teardown_error::test_body_passes",)),
    "collection_error": Expected(0, 0, 1, 0, ("test_collection_error",)),
    "chatty": Expected(1, 0, 0, 0, ()),
}
ERROR_TASKS = ("setup_error", "teardown_error", "collection_error")


def _run(built: core.Implementation, task: str, cancel: Cancel, until: Any) -> tuple[Any, Any, Any]:
    command = built.extras["tasks"][task]
    t = ticket(task, EffectFacetClass.EVENT)
    confirmation, result = built.impl.run(command, t, cancel, until)
    return confirmation, result, t


def _run_to_end(built: core.Implementation, task: str) -> tuple[Any, Any]:
    confirmation, result, t = _run(built, task, Cancel(), FUTURE)
    assert status(confirmation) is ConfirmationStatus.APPLIED, task
    assert result is not None, task
    return result, t


def _klass(result: Any) -> ports.ExecutionClass:
    return ports.ExecutionClass(result.classification)


def _counts(result: Any) -> tuple[int, int, int, int]:
    counts = result.counts
    assert counts is not None
    return counts.passed, counts.failed, counts.errors, counts.skipped


def counts_include_setup_teardown_and_collection_errors(built: core.Implementation) -> None:
    for task in ERROR_TASKS:
        want = EXPECTED[task]
        result, _ = _run_to_end(built, task)
        assert _counts(result) == (want.passed, want.failed, want.errors, want.skipped), task
        assert result.counts.errors >= 1, task  # the error is counted, never dropped
        assert _klass(result) is ports.ExecutionClass.FAILED, task
        assert result.recorded.passed is False and result.recorded.counts is not None, task


def passing_run_reports_its_counts(built: core.Implementation) -> None:
    result, _ = _run_to_end(built, "passing")
    want = EXPECTED["passing"]
    assert _klass(result) is ports.ExecutionClass.PASSED
    assert _counts(result) == (want.passed, want.failed, want.errors, want.skipped)
    assert result.failing == ()
    assert result.recorded.passed is True and result.recorded.counts is not None  # V-5.5


def failing_ids_are_returned(built: core.Implementation) -> None:
    result, _ = _run_to_end(built, "failing")
    want = EXPECTED["failing"]
    assert _klass(result) is ports.ExecutionClass.FAILED
    assert _counts(result) == (want.passed, want.failed, want.errors, want.skipped)
    assert tuple(result.failing) == want.failing
    assert len(" ".join(result.failing).encode()) <= TEXT_MAX  # bounded evidence (V-13)
    for task in ERROR_TASKS:  # an errored test is a failing id too
        errored, _ = _run_to_end(built, task)
        assert tuple(errored.failing) == EXPECTED[task].failing, task


def missing_counts_is_a_contract_violation(built: core.Implementation) -> None:
    # console text says "passed" and no report exists: the result is never inferred from it
    result, _ = _run_to_end(built, "console_lies")
    assert _klass(result) is ports.ExecutionClass.CONTRACT_VIOLATION
    assert result.counts is None
    assert result.recorded.passed is False


def exit_status_and_report_that_disagree_are_a_contract_violation(
    built: core.Implementation,
) -> None:
    result, _ = _run_to_end(built, "exit_disagrees")
    assert _klass(result) is ports.ExecutionClass.CONTRACT_VIOLATION
    assert result.recorded.passed is False


def full_output_stays_behind_a_handle(built: core.Implementation) -> None:
    result, t = _run_to_end(built, "chatty")
    handle = built.impl.output(t)
    assert handle is not None
    data = handle.read()
    text = data.decode("utf-8")
    assert handle.size == len(data) and handle.sha256 == hashlib.sha256(data).hexdigest()
    # the handle holds everything; the result holds only a bounded tail of it
    assert HEAD in text and TAIL in text
    assert len(result.excerpt.encode("utf-8")) <= TEXT_MAX < handle.size
    assert HEAD not in result.excerpt and TAIL in result.excerpt
    assert text.endswith(result.excerpt)
    # a second run of the same task under another ticket has its own handle
    _, other = _run_to_end(built, "chatty")
    again = built.impl.output(other)
    assert again is not None and HEAD in again.read().decode("utf-8")


def cancel_and_until_yield_interrupted_with_code(built: core.Implementation) -> None:
    for cancel, until, code in (
        (Cancel(StopCause.CANCEL), FUTURE, EXECUTION_CANCELLED),
        (Cancel(StopCause.RELEASE_POINT), FUTURE, EXECUTION_DEADLINE),
        (Cancel(), PAST, EXECUTION_DEADLINE),
    ):
        confirmation, result, _ = _run(built, "long", cancel, until)
        assert status(confirmation) is ConfirmationStatus.APPLIED  # it started, then was ended
        assert result is not None  # never None for a started process
        assert _klass(result) is ports.ExecutionClass.INTERRUPTED
        assert result.code == code
        assert result.recorded.passed is False


def descriptor_is_in_run_group_and_equal_across_calls(built: core.Implementation) -> None:
    for name, command in built.extras["tasks"].items():
        call = effect_call(
            "run", {"command": command, "cancel": Cancel(), "until": FUTURE}, effect=name
        )
        first = ports.as_descriptor(built.impl.release_descriptor(call))
        assert isinstance(first, ports.InRunGroup), name
        assert first.helpers_disclosed == (
            built.impl.policy(command).helpers == ports.Helpers.DISCLOSED
        )
        assert ports.as_descriptor(built.impl.release_descriptor(call)) == first  # V-10.1
        policy = built.impl.policy(command)
        assert policy == built.impl.policy(command)  # pure
        assert ports.SelfProvisioning(policy.self_provisioning) is not None


def none_result_only_with_not_applied(built: core.Implementation) -> None:
    for task in ("passing", "console_lies", "exit_disagrees"):
        confirmation, result, _ = _run(built, task, Cancel(), FUTURE)
        assert result is not None or status(confirmation) is ConfirmationStatus.NOT_APPLIED


CASES: Sequence[core.Case] = (
    core.Case(
        "counts_include_setup_teardown_and_collection_errors",
        counts_include_setup_teardown_and_collection_errors,
    ),
    core.Case("passing_run_reports_its_counts", passing_run_reports_its_counts),
    core.Case("failing_ids_are_returned", failing_ids_are_returned),
    core.Case("missing_counts_is_a_contract_violation", missing_counts_is_a_contract_violation),
    core.Case(
        "exit_status_and_report_that_disagree_are_a_contract_violation",
        exit_status_and_report_that_disagree_are_a_contract_violation,
    ),
    core.Case("full_output_stays_behind_a_handle", full_output_stays_behind_a_handle),
    core.Case(
        "cancel_and_until_yield_interrupted_with_code",
        cancel_and_until_yield_interrupted_with_code,
    ),
    core.Case(
        "descriptor_is_in_run_group_and_equal_across_calls",
        descriptor_is_in_run_group_and_equal_across_calls,
    ),
    core.Case("none_result_only_with_not_applied", none_result_only_with_not_applied),
)

core.register_family(FAMILY, CASES)
