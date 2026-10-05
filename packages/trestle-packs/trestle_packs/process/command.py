"""Real `ExecutionPort`: one allowlisted command, run once, in the run's process group
(L.SL-3.2; B3-C14, B3-C3, V-2.3, WR-CANCEL-5).

`CommandPort.run(command, ticket, cancel, until)` starts `command.argv` (whose first element is
`command.resolved.executable`, an absolute path) as a child of the calling process:

- **in the run's process group, no new session**: the child inherits the caller's process group
  and session, so every process it starts stays attributable to the run (V-2.3); the port never
  detaches a process it starts (B3-C4);
- **stdin closed** (`/dev/null`: it reads EOF), so nothing waits for a person (WR-CANCEL-5);
- **environment built from empty**: `command.environment` plus a scrubbed `PATH` that holds only
  the resolved executable's directory, `/usr/bin` and `/bin` (never a shim directory);
- **ended on `cancel` or at `until`**, even when either has already passed when the port is called
  (the ticket is issued, so the effect is applied and then ended): SIGTERM, then SIGKILL after a
  grace, of the child only (the run's group belongs to the run); the result is `APPLIED` with an
  `INTERRUPTED` result whose code comes from `cancel.cause()` (`CANCEL` -> `EXECUTION_CANCELLED`;
  `RELEASE_POINT`, or `until` passing -> `EXECUTION_DEADLINE`), never `None`.

A result is never inferred from console text (the console tail is only the `excerpt`). A test
selector (`reports_tests`) reports its counts in a JUnit XML file: the port gives the command a
private path in `TRESTLE_TEST_REPORT` (and to pytest through `PYTEST_ADDOPTS=--junitxml=...`); a
missing or unreadable report, or an exit status that disagrees with the report, is
`CONTRACT_VIOLATION`.

Executor-chosen values (the contract names none): the poll interval, the SIGTERM-to-SIGKILL grace,
the `TRESTLE_TEST_REPORT` handover, the 512-byte total for `failing` (V-13's EVENT_MAX under
`TRESTLE_TEST_LIMITS`) and for the excerpt (TEXT_MAX), the code None on a plain failed command.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import xml.etree.ElementTree as ElementTree
from datetime import UTC, datetime
from pathlib import Path

from trestle.workflow.ports import (
    BoundCommand,
    EffectCall,
    ExecutionClass,
    ExecutionPolicy,
    ExecutionResult,
    Helpers,
    InRunGroup,
    SelfProvisioning,
)
from trestle.workflow.services import AttemptTicket
from trestle.workflow.values import (
    CancelSignal,
    Confirmation,
    ConfirmationStatus,
    StopCause,
    TestCounts,
)

# Stable codes (B3-C14), spelled as the vocabulary spells them; the drift test reads both.
EXECUTION_CANCELLED = "execution.cancelled"
EXECUTION_DEADLINE = "execution.deadline_exceeded"
TOOLCHAIN_MISSING = "execution.toolchain_missing"

REPORT_ENV = "TRESTLE_TEST_REPORT"
_POLL_S = 0.05  # how often a running child is checked against cancel and the deadline
_GRACE_S = 2.0  # SIGTERM to SIGKILL
_TEXT_MAX = 512  # V-13 TEXT_MAX / EVENT_MAX under TRESTLE_TEST_LIMITS
_SAFE_DIRS = ("/usr/bin", "/bin")


def scrubbed_path(executable: str) -> str:
    """`PATH` for a command: its own executable's directory, then the system's (no shims)."""
    return os.pathsep.join((os.path.dirname(executable), *_SAFE_DIRS))


def _tail(data: bytes, limit: int = _TEXT_MAX) -> str:
    return data[-limit:].decode("utf-8", "ignore")


def _failing(ids: list[str]) -> tuple[str, ...]:
    kept: list[str] = []
    used = 0
    for item in ids:
        size = len(item.encode("utf-8"))
        if used + size > _TEXT_MAX:
            break
        kept.append(item)
        used += size
    return tuple(kept)


def read_report(path: Path) -> tuple[TestCounts, list[str]] | None:
    """Counts and failing test ids from a JUnit XML report; None when it is absent or unreadable."""
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
    failing = [
        f"{case.get('classname')}::{case.get('name')}"
        if case.get("classname")
        else case.get("name") or ""
        for case in root.iter("testcase")
        if case.find("failure") is not None or case.find("error") is not None
    ]
    counts = TestCounts(
        passed=max(total - failures - errors - skipped, 0),
        failed=failures,
        errors=errors,
        skipped=skipped,
    )
    return counts, failing


class CommandPort:
    """`ExecutionPort` over real child processes."""

    def policy(self, command: BoundCommand) -> ExecutionPolicy:
        """Pure: a task runs with interpreter and dependency downloads off and no helper that
        outlives the run (B3-C14, Python and pytest commands)."""
        return ExecutionPolicy(SelfProvisioning.DISABLED_BY_CONFIGURATION, Helpers.PREVENTED, None)

    def release_descriptor(self, call: EffectCall) -> InRunGroup:
        """`InRunGroup(helpers_disclosed = policy(command).helpers is DISCLOSED)` (B3-C3),
        derived from the call alone, before any process starts (B3-I4)."""
        command = call.arguments.get("command")
        if isinstance(command, BoundCommand):
            return InRunGroup(self.policy(command).helpers is Helpers.DISCLOSED)
        return InRunGroup(False)

    def run(
        self,
        command: BoundCommand,
        ticket: AttemptTicket,
        cancel: CancelSignal,
        until: datetime,
    ) -> tuple[Confirmation, ExecutionResult | None]:
        argv = tuple(command.argv)
        executable = command.resolved.executable
        if argv[:1] != (executable,) or not os.path.isabs(executable):
            raise ValueError(
                "command.argv[0] must be command.resolved.executable, absolute (B3-C14)"
            )
        with tempfile.TemporaryDirectory(prefix="trestle-cmd-") as scratch:
            report = Path(scratch) / "report.xml"
            env = dict(command.environment)
            env["PATH"] = scrubbed_path(executable)
            if command.reports_tests:
                env[REPORT_ENV] = str(report)
                addopts = env.get("PYTEST_ADDOPTS", "")
                env["PYTEST_ADDOPTS"] = f"{addopts} --junitxml={report}".strip()
            console = Path(scratch) / "console.log"
            with console.open("wb") as sink:
                try:
                    proc = subprocess.Popen(
                        argv,
                        env=env,
                        stdin=subprocess.DEVNULL,
                        stdout=sink,
                        stderr=subprocess.STDOUT,
                        shell=False,
                    )
                except (FileNotFoundError, PermissionError, NotADirectoryError):
                    return Confirmation(
                        ConfirmationStatus.NOT_APPLIED, TOOLCHAIN_MISSING, None
                    ), None
                interrupted = self._watch(proc, cancel, until)
            excerpt = _tail(console.read_bytes())
            applied = Confirmation(ConfirmationStatus.APPLIED, None, None)
            status = proc.returncode
            if interrupted is not None:
                return applied, ExecutionResult(
                    status, ExecutionClass.INTERRUPTED, None, (), interrupted, excerpt
                )
            if not command.reports_tests:
                klass = ExecutionClass.PASSED if status == 0 else ExecutionClass.FAILED
                return applied, ExecutionResult(status, klass, None, (), None, excerpt)
            return applied, self._test_result(status, read_report(report), excerpt)

    # ------------------------------------------------------------------

    def _watch(
        self, proc: subprocess.Popen[bytes], cancel: CancelSignal, until: datetime
    ) -> str | None:
        """Wait for the child; on cancel or the deadline end it and return the interrupt code."""
        while True:
            if proc.poll() is not None:
                return None
            code = self._interrupt_code(cancel, until)
            if code is not None:
                self._end(proc)
                return code
            try:
                proc.wait(timeout=_POLL_S)
            except subprocess.TimeoutExpired:
                continue
            except BaseException:
                self._end(proc)
                raise

    def _interrupt_code(self, cancel: CancelSignal, until: datetime) -> str | None:
        if cancel.requested:
            return (
                EXECUTION_CANCELLED
                if cancel.cause() in (StopCause.CANCEL, None)
                else EXECUTION_DEADLINE
            )
        moment = until if until.tzinfo is not None else until.replace(tzinfo=UTC)
        if datetime.now(UTC) >= moment:
            return EXECUTION_DEADLINE
        return None

    def _end(self, proc: subprocess.Popen[bytes]) -> None:
        proc.terminate()
        try:
            proc.wait(timeout=_GRACE_S)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

    def _test_result(
        self, status: int, report: tuple[TestCounts, list[str]] | None, excerpt: str
    ) -> ExecutionResult:
        if report is None:  # a test selector with no counts is never inferred (B3-C14)
            return ExecutionResult(
                status, ExecutionClass.CONTRACT_VIOLATION, None, (), None, excerpt
            )
        counts, failing = report
        bad = counts.failed + counts.errors
        if (status == 0) != (bad == 0):  # exit status and report disagree
            return ExecutionResult(
                status, ExecutionClass.CONTRACT_VIOLATION, counts, _failing(failing), None, excerpt
            )
        klass = ExecutionClass.PASSED if status == 0 else ExecutionClass.FAILED
        return ExecutionResult(status, klass, counts, _failing(failing), None, excerpt)
