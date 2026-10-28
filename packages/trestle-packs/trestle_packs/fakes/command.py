"""Fake command execution port (L.SV-5.16; B3-C14, B3-C3, MC-25).

Stdlib only. Until BFD-47 lands at SL-3 a pack does not import `trestle.workflow`, so every value
this fake returns is defined here with the field names of the B3 / vocabulary type it stands for
(structural conformance, DM-09): `Confirmation`, `ExecutionResult`, `ExecutionPolicy`,
`BoundCommand` and their small parts. A release descriptor is returned as its wire mapping.
The shapes shared with `marker.py` (`Confirmation`, `ConfirmationStatus`, `in_run_group`,
`durable`) live here.

`FakeCommand(results)` implements `ExecutionPort`: `results` maps a command's `task` to the result
`run` gives for it, or is a sequence used in order (the last result repeats). A result may be an
`ExecutionResult`, or a bool (`True` passed, `False` failed). It runs nothing.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

# Stable codes the port puts on an interrupted run (B3-C14), spelled as the vocabulary spells them
# (the SA-14 tests read both spellings and require them to agree).
EXECUTION_CANCELLED = "execution.cancelled"
EXECUTION_DEADLINE = "execution.deadline_exceeded"
ROUTE_UNSUPPORTED = "admission.route_unsupported"


class ConfirmationStatus(StrEnum):
    APPLIED = "applied"
    NOT_APPLIED = "not_applied"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class Confirmation:
    status: ConfirmationStatus
    code: str | None
    identity: str | None


class ExecutionClass(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    CONTRACT_VIOLATION = "contract_violation"
    INTERRUPTED = "interrupted"


class SelfProvisioning(StrEnum):
    DISABLED_BY_CONFIGURATION = "disabled_by_configuration"
    BLOCKS = "blocks"


class Helpers(StrEnum):
    PREVENTED = "prevented"
    CONTAINED = "contained"
    DISCLOSED = "disclosed"


@dataclass(frozen=True, slots=True)
class ExecutionPolicy:
    self_provisioning: SelfProvisioning
    helpers: Helpers
    disclosure: str | None


@dataclass(frozen=True, slots=True)
class TestCounts:
    __test__ = False  # not a pytest class

    passed: int
    failed: int
    errors: int
    skipped: int


@dataclass(frozen=True, slots=True)
class RecordedResult:
    passed: bool
    code: str | None
    counts: TestCounts | None


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    exit_status: int
    classification: ExecutionClass
    counts: TestCounts | None
    failing: tuple[str, ...]
    code: str | None
    excerpt: str

    @property
    def recorded(self) -> RecordedResult:
        return RecordedResult(self.classification is ExecutionClass.PASSED, self.code, self.counts)


@dataclass(frozen=True, slots=True)
class Resolved:
    executable: str
    reported_version: str
    pin_fingerprint: str
    adoption_fingerprint: str


@dataclass(frozen=True, slots=True)
class BoundCommand:
    task: str
    argv: tuple[str, ...]
    environment: Mapping[str, str]
    resolved: Resolved
    reports_tests: bool


def in_run_group(helpers_disclosed: bool = False) -> dict[str, Any]:
    """`InRunGroup` as its wire mapping (V-10)."""
    return {"form": "in_run_group", "helpers_disclosed": helpers_disclosed}


def durable(owner: str) -> dict[str, Any]:
    """`Durable(owner)` as its wire mapping (V-10); `owner` is `host` or `environment`."""
    return {"form": "durable", "owner": owner}


def _value(x: Any) -> Any:
    return getattr(x, "value", x)


def passed_result(counts: TestCounts | None = None) -> ExecutionResult:
    return ExecutionResult(0, ExecutionClass.PASSED, counts, (), None, "")


def failed_result(
    code: str | None = "test.failed", counts: TestCounts | None = None
) -> ExecutionResult:
    return ExecutionResult(1, ExecutionClass.FAILED, counts, (), code, "")


type Scripted = ExecutionResult | bool


class FakeCommand:
    """An `ExecutionPort` that returns scripted results and runs nothing."""

    def __init__(
        self,
        results: Mapping[str, Scripted] | Sequence[Scripted] = (),
        *,
        policy: ExecutionPolicy | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._results = results
        self._policy = policy or ExecutionPolicy(
            SelfProvisioning.DISABLED_BY_CONFIGURATION, Helpers.PREVENTED, None
        )
        self._now = now or (lambda: datetime.now(UTC))
        self._next = 0
        self.runs: list[str] = []  # the tasks run, in order

    def policy(self, command: Any) -> ExecutionPolicy:
        return self._policy

    def release_descriptor(self, call: Any) -> dict[str, Any]:
        """`InRunGroup(helpers_disclosed = policy(command).helpers is DISCLOSED)` (B3-C3)."""
        command = call.arguments.get("command")
        helpers = self._policy if command is None else self.policy(command)
        return in_run_group(helpers.helpers is Helpers.DISCLOSED)

    def run(
        self, command: Any, ticket: Any, cancel: Any, until: datetime
    ) -> tuple[Confirmation, ExecutionResult | None]:
        if tuple(command.argv)[:1] != (command.resolved.executable,):
            raise ValueError("command.argv[0] must equal command.resolved.executable (B3-C14)")
        self.runs.append(command.task)
        if cancel.requested:
            cause = cancel.cause()
            code = EXECUTION_CANCELLED if _value(cause) == "cancel" else EXECUTION_DEADLINE
            return self._applied(
                ExecutionResult(-15, ExecutionClass.INTERRUPTED, None, (), code, "")
            )
        if self._now() >= until:
            return self._applied(
                ExecutionResult(-15, ExecutionClass.INTERRUPTED, None, (), EXECUTION_DEADLINE, "")
            )
        result = self._scripted(command.task)
        if command.reports_tests and result.counts is None:
            # a test selector's result without counts is never inferred (B3-C14)
            result = ExecutionResult(
                result.exit_status,
                ExecutionClass.CONTRACT_VIOLATION,
                None,
                result.failing,
                result.code,
                result.excerpt,
            )
        return self._applied(result)

    # ------------------------------------------------------------------

    def _applied(self, result: ExecutionResult) -> tuple[Confirmation, ExecutionResult]:
        return Confirmation(ConfirmationStatus.APPLIED, None, None), result

    def _scripted(self, task: str) -> ExecutionResult:
        if isinstance(self._results, Mapping):
            scripted: Scripted = self._results.get(task, True)
        elif self._results:
            scripted = self._results[min(self._next, len(self._results) - 1)]
            self._next += 1
        else:
            scripted = True
        if isinstance(scripted, bool):
            return passed_result() if scripted else failed_result()
        return scripted
