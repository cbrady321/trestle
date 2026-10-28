"""Pytest-selector runner: out of process, through an `ExecutionPort`, result read from JUnit
(L.RB-5.1; B3-C14, B3-C21, B9.2, WR-VERIFY-4:runner-counts-errors).

`PytestJunitRunner(execution, artifacts)` is itself an `ExecutionPort`. For a test selector
(`command.reports_tests`) it runs the bound pytest command through the wrapped port with two
additions, both under `artifacts/<root run>/<node path>/<effect>.<attempt>/`:

- `--junitxml=<dir>/junit.xml` is appended to the argv: the counts come from that artifact and from
  nothing else (a result is never inferred from console text, B3-C14);
- the whole console (stdout and stderr) goes to `<dir>/output.log`: the child's first act is to
  point its own stdout and stderr at that file and `exec` the selector, so the wrapped port still
  runs and ends exactly one process (its cancel and deadline handling, its process group and its
  empty environment are untouched) and the runner needs no second process. The full output stays
  behind an `OutputHandle` (`output(ticket)`); the `ExecutionResult` carries only a bounded tail
  as its `excerpt` (B9.2).

What it decides from the artifact:

- counts are the JUnit testsuite totals, so setup, teardown and collection errors are counted as
  `errors` (pytest records each as an `<error>`); `failing` lists the `classname::name` of every
  failed or errored case, capped at 512 bytes in all (V-13);
- a missing or unreadable artifact, or an exit status that disagrees with it (zero with failures
  or errors, non-zero with none: for example pytest's "no tests collected"), is
  `CONTRACT_VIOLATION`, never a pass or a plain failure (B3-C14);
- an interrupted run is the wrapped port's `INTERRUPTED` result with the code it chose; a
  `NOT_APPLIED` confirmation (nothing started) is returned unchanged.

A command that does not report tests is passed through to the wrapped port untouched.

Executor-chosen values (the contract names none): the artifact layout above, the 512-byte excerpt
and `failing` bounds (V-13 TEXT_MAX / EVENT_MAX under `TRESTLE_TEST_LIMITS`, as `CommandPort`), the
launcher program (`_LAUNCH`), and the junit id form `classname::name` (the form `CommandPort`
reports, so both runners name a failing test alike).
"""

from __future__ import annotations

import hashlib
import os
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from trestle.workflow.ports import (
    BoundCommand,
    EffectCall,
    ExecutionClass,
    ExecutionPolicy,
    ExecutionPort,
    ExecutionResult,
    ReleaseDescriptor,
)
from trestle.workflow.services import AttemptTicket
from trestle.workflow.values import CancelSignal, Confirmation, TestCounts

TEXT_MAX = 512  # V-13 TEXT_MAX / EVENT_MAX under TRESTLE_TEST_LIMITS
JUNIT_NAME = "junit.xml"
OUTPUT_NAME = "output.log"

# argv = (exe, "-c", _LAUNCH, <output file>, exe, <selector args...>): point stdout and stderr at
# the output file, then replace this process with the selector, so no second process ever exists.
_LAUNCH = (
    "import os, sys\n"
    "fd = os.open(sys.argv[1], os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)\n"
    "os.dup2(fd, 1)\n"
    "os.dup2(fd, 2)\n"
    "os.close(fd)\n"
    "os.execv(sys.argv[2], sys.argv[2:])\n"
)


@dataclass(frozen=True, slots=True)
class OutputHandle:
    """The full console output of one run, kept as an artifact (B9.2)."""

    path: str
    size: int
    sha256: str

    def read(self) -> bytes:
        return Path(self.path).read_bytes()


def read_junit(path: Path) -> tuple[TestCounts, tuple[str, ...]] | None:
    """Counts and failing ids from a JUnit XML artifact; None when it is absent or unreadable.

    Totals are summed over every `<testsuite>` (pytest writes one; a collection error or a setup
    or teardown error is an `<error>` in it and counts as one of `errors`)."""
    try:
        root = ElementTree.parse(path).getroot()
    except (OSError, ElementTree.ParseError):
        return None
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    if not suites:
        return None
    try:
        total = sum(int(s.get("tests", "0")) for s in suites)
        failures = sum(int(s.get("failures", "0")) for s in suites)
        errors = sum(int(s.get("errors", "0")) for s in suites)
        skipped = sum(int(s.get("skipped", "0")) for s in suites)
    except ValueError:
        return None
    ids: list[str] = []
    for case in root.iter("testcase"):
        if case.find("failure") is None and case.find("error") is None:
            continue
        classname, name = case.get("classname"), case.get("name") or ""
        ids.append(f"{classname}::{name}" if classname else name)
    counts = TestCounts(
        passed=max(total - failures - errors - skipped, 0),
        failed=failures,
        errors=errors,
        skipped=skipped,
    )
    return counts, _capped(ids)


def _capped(ids: list[str]) -> tuple[str, ...]:
    kept: list[str] = []
    used = 0
    for item in ids:
        size = len(item.encode("utf-8"))
        if used + size > TEXT_MAX:
            break
        kept.append(item)
        used += size
    return tuple(kept)


def _tail(data: bytes) -> str:
    return data[-TEXT_MAX:].decode("utf-8", "ignore")


class PytestJunitRunner:
    """`ExecutionPort` that runs a pytest selector through `execution` and reads JUnit."""

    def __init__(self, execution: ExecutionPort, artifacts: Path) -> None:
        self._execution = execution
        self._artifacts = Path(artifacts)
        self._outputs: dict[tuple[str, tuple[str, ...], str, int], OutputHandle] = {}

    def policy(self, command: BoundCommand) -> ExecutionPolicy:
        return self._execution.policy(command)

    def release_descriptor(self, call: EffectCall) -> ReleaseDescriptor:
        """The wrapped port's: `InRunGroup(helpers_disclosed)` (B3-C3), before anything runs."""
        return self._execution.release_descriptor(call)

    def output(self, ticket: AttemptTicket) -> OutputHandle | None:
        """The full output of the run this ticket ran, or None when nothing started."""
        return self._outputs.get(self._key(ticket))

    def run(
        self,
        command: BoundCommand,
        ticket: AttemptTicket,
        cancel: CancelSignal,
        until: datetime,
    ) -> tuple[Confirmation, ExecutionResult | None]:
        if not command.reports_tests:
            return self._execution.run(command, ticket, cancel, until)
        executable = command.resolved.executable
        if tuple(command.argv)[:1] != (executable,) or not os.path.isabs(executable):
            raise ValueError(
                "command.argv[0] must be command.resolved.executable, absolute (B3-C14)"
            )
        directory = self._directory(ticket)
        directory.mkdir(parents=True, exist_ok=True)
        junit, output = directory / JUNIT_NAME, directory / OUTPUT_NAME
        junit.unlink(missing_ok=True)
        output.unlink(missing_ok=True)
        launched = replace(
            command,
            argv=(
                executable,
                "-c",
                _LAUNCH,
                str(output),
                *command.argv,
                f"--junitxml={junit}",
            ),
            reports_tests=False,  # this runner reads the artifact, not the wrapped port
        )
        confirmation, result = self._execution.run(launched, ticket, cancel, until)
        if result is None or not output.exists():
            return confirmation, result
        data = output.read_bytes()
        self._outputs[self._key(ticket)] = OutputHandle(
            str(output), len(data), hashlib.sha256(data).hexdigest()
        )
        excerpt = _tail(data)
        if ExecutionClass(result.classification) is ExecutionClass.INTERRUPTED:
            return confirmation, replace(result, excerpt=excerpt)
        return confirmation, self._classified(result.exit_status, read_junit(junit), excerpt)

    # ------------------------------------------------------------------

    def _classified(
        self, status: int, report: tuple[TestCounts, tuple[str, ...]] | None, excerpt: str
    ) -> ExecutionResult:
        if report is None:  # counts are never inferred from the console (B3-C14)
            return ExecutionResult(
                status, ExecutionClass.CONTRACT_VIOLATION, None, (), None, excerpt
            )
        counts, failing = report
        bad = counts.failed + counts.errors
        if (status == 0) != (bad == 0):  # exit status and artifact disagree (WR-VERIFY-5)
            return ExecutionResult(
                status, ExecutionClass.CONTRACT_VIOLATION, counts, failing, None, excerpt
            )
        klass = ExecutionClass.PASSED if status == 0 else ExecutionClass.FAILED
        return ExecutionResult(status, klass, counts, failing, None, excerpt)

    @staticmethod
    def _key(ticket: AttemptTicket) -> tuple[str, tuple[str, ...], str, int]:
        lineage = ticket.lineage
        return (
            str(lineage.root_run_id),
            tuple(lineage.path.segments),
            ticket.effect,
            ticket.attempt,
        )

    def _directory(self, ticket: AttemptTicket) -> Path:
        run, path, effect, attempt = self._key(ticket)
        return self._artifacts / run / ".".join(path) / f"{effect}.{attempt}"
