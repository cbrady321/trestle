"""L.SL-3.2: the real `ExecutionPort` (B3-C14, B3-C3, V-2.3, WR-CANCEL-5). The shared conformance
suite runs against it in `tests/proof/suites/ports` (`[real-command]`); these tests run real
children and read what the child itself saw."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from trestle_packs.process import command as command_mod
from trestle_packs.process.command import CommandPort

from trestle.common import clock
from trestle.common.plan import vocabulary
from trestle.workflow import ports
from trestle.workflow import services as svc
from trestle.workflow.declarations import EffectFacetClass, Lifetime, Repeat
from trestle.workflow.values import ConfirmationStatus, Lineage, NodePath, StopCause

FUTURE = datetime(2999, 1, 1, tzinfo=UTC)
PAST = datetime(2000, 1, 1, tzinfo=UTC)
LINEAGE = Lineage("r_proc_0001", NodePath(("proc",)))
RESOLVED = ports.Resolved(sys.executable, "3.12", "pin", "adoption")


class Cancel:
    """A `CancelSignal` a test fires from another thread."""

    def __init__(self) -> None:
        self._cause: StopCause | None = None
        self._fired = threading.Event()

    def fire(self, cause: StopCause) -> None:
        self._cause = cause
        self._fired.set()

    @property
    def requested(self) -> bool:
        return self._fired.is_set()

    def cause(self) -> StopCause | None:
        return self._cause

    def wait(self, timeout: timedelta) -> bool:
        return self._fired.wait(timeout.total_seconds())


def _ticket() -> svc.AttemptTicket:
    return svc.AttemptTicket(
        LINEAGE,
        "test",
        EffectFacetClass.EVENT,
        1,
        Repeat.SAFE,
        Lifetime.RUN,
        ports.InRunGroup(),
        None,
    )


def _command(program: str, *args: str, reports_tests: bool = False) -> ports.BoundCommand:
    argv = (sys.executable, "-c", textwrap.dedent(program), *args)
    return ports.BoundCommand("task", argv, {}, RESOLVED, reports_tests)


def _run(command: ports.BoundCommand, cancel: Any = None, until: datetime = FUTURE) -> Any:
    return CommandPort().run(command, _ticket(), cancel or Cancel(), until)


def test_codes_are_spelled_as_the_vocabulary_spells_them() -> None:
    assert command_mod.EXECUTION_CANCELLED == vocabulary.EXECUTION_CANCELLED
    assert command_mod.EXECUTION_DEADLINE == vocabulary.EXECUTION_DEADLINE


@pytest.mark.proves(
    "WR-CANCEL-5",
    "WR-CANCEL-5:A-bound-command-never-detached",
    "A",
    "single",
    "STUB+MCP",
    "BOTH",
)
def test_child_pgid_equals_run_group(tmp_path: Path) -> None:
    """The child is started in the calling (run) process group and session: no new session, no
    new group, so every process it starts is attributable to the run (V-2.3, B3-C4)."""
    seen = tmp_path / "ids.txt"
    program = """
        import os, sys
        open(sys.argv[1], "w").write(f"{os.getpgid(0)} {os.getsid(0)} {os.getppid()}")
    """
    confirmation, result = _run(_command(program, str(seen)))
    assert confirmation.status is ConfirmationStatus.APPLIED
    assert result is not None and result.classification is ports.ExecutionClass.PASSED
    pgid, sid, ppid = (int(v) for v in seen.read_text().split())
    assert pgid == os.getpgid(0)  # the run's group
    assert sid == os.getsid(0)  # no new session (never detached)
    assert ppid == os.getpid()  # a direct child of the caller


def test_stdin_reads_eof_and_path_is_scrubbed(tmp_path: Path) -> None:
    seen = tmp_path / "env.txt"
    program = """
        import os, sys
        data = sys.stdin.read()
        open(sys.argv[1], "w").write(f"{len(data)}\\n{os.environ['PATH']}\\n{sorted(os.environ)}")
    """
    os.environ["TRESTLE_PLANTED_LEAK"] = "1"  # the caller's environment never reaches the child
    try:
        _, result = _run(_command(program, str(seen)))
    finally:
        del os.environ["TRESTLE_PLANTED_LEAK"]
    assert result is not None and result.classification is ports.ExecutionClass.PASSED
    length, path, names = seen.read_text().split("\n", 2)
    assert length == "0"  # stdin reads EOF at once (WR-CANCEL-5)
    assert path == command_mod.scrubbed_path(sys.executable)
    assert path.split(os.pathsep)[0] == os.path.dirname(sys.executable)
    assert "TRESTLE_PLANTED_LEAK" not in names


def test_release_descriptor_is_pure_and_starts_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[Any] = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: started.append(a))
    port = CommandPort()
    command = _command("pass")
    call = ports.EffectCall(
        "run", {"command": command}, LINEAGE, "test", Lifetime.RUN, timedelta(seconds=1)
    )
    first = ports.as_descriptor(port.release_descriptor(call))
    assert first == ports.InRunGroup(helpers_disclosed=False)
    assert first == ports.as_descriptor(port.release_descriptor(call))
    assert port.policy(command) == port.policy(command)
    assert started == []  # the descriptor is derived before, and without, any process


@pytest.mark.proves(
    "WR-CANCEL-5",
    "WR-CANCEL-5:A-bound-command-never-detached",
    "A",
    "single",
    "STUB+MCP",
    "BOTH",
)
def test_interrupted_result_coded_from_cause(tmp_path: Path) -> None:
    """A cancel or a passed deadline after the start ends the child and returns APPLIED with an
    INTERRUPTED result coded from `cancel.cause()`; never None (B3-C14)."""
    cases = (
        (StopCause.CANCEL, command_mod.EXECUTION_CANCELLED),
        (StopCause.RELEASE_POINT, command_mod.EXECUTION_DEADLINE),
    )
    for index, (cause, code) in enumerate(cases):
        started = tmp_path / f"started-{index}"
        program = """
            import signal, sys
            open(sys.argv[1], "w").write("up")
            signal.pause()
        """
        cancel = Cancel()

        def fire_when_started(
            cancel: Cancel = cancel, cause: StopCause = cause, at: Path = started
        ) -> None:
            while not at.exists():
                cancel.wait(timedelta(seconds=clock.poll_interval))
            cancel.fire(cause)

        firer = threading.Thread(target=fire_when_started)
        firer.start()
        confirmation, result = _run(_command(program, str(started)), cancel)
        firer.join()
        assert started.exists()  # it was running when it was ended
        assert confirmation.status is ConfirmationStatus.APPLIED
        assert result is not None
        assert result.classification is ports.ExecutionClass.INTERRUPTED
        assert result.code == code
        assert result.exit_status < 0  # ended by a signal
        assert result.recorded.passed is False
    # `until` passing mid-run is the deadline, whatever the cancel says
    soon = datetime.now(UTC) + timedelta(seconds=clock.poll_interval)
    _, result = _run(_command("import signal; signal.pause()"), Cancel(), soon)
    assert result is not None and result.classification is ports.ExecutionClass.INTERRUPTED
    assert result.code == command_mod.EXECUTION_DEADLINE


def test_a_command_that_ignores_sigterm_is_still_ended(tmp_path: Path) -> None:
    started = tmp_path / "up"
    program = """
        import signal, sys
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        open(sys.argv[1], "w").write("up")
        signal.pause()
    """
    cancel = Cancel()

    def fire() -> None:
        while not started.exists():
            cancel.wait(timedelta(seconds=clock.poll_interval))
        cancel.fire(StopCause.CANCEL)

    firer = threading.Thread(target=fire)
    firer.start()
    _, result = _run(_command(program, str(started)), cancel)
    firer.join()
    assert result is not None and result.classification is ports.ExecutionClass.INTERRUPTED
    assert result.code == command_mod.EXECUTION_CANCELLED


def test_test_selector_counts_come_from_the_report_not_the_console(tmp_path: Path) -> None:
    report = """
        import os
        open(os.environ["TRESTLE_TEST_REPORT"], "w").write(
            '<testsuites><testsuite tests="5" failures="1" errors="1" skipped="1">'
            '<testcase classname="t" name="ok"/>'
            '<testcase classname="t" name="bad"><failure/></testcase>'
            '<testcase classname="t" name="boom"><error/></testcase>'
            '</testsuite></testsuites>')
        print("42 passed, 0 failed")  # console text is evidence, never protocol
        raise SystemExit(1)
    """
    _, result = _run(_command(report, reports_tests=True))
    assert result is not None and result.classification is ports.ExecutionClass.FAILED
    assert result.counts == ports.TestCounts(passed=2, failed=1, errors=1, skipped=1)
    assert result.failing == ("t::bad", "t::boom")
    assert "42 passed" in result.excerpt
    assert result.recorded.counts == result.counts and result.recorded.passed is False


def test_report_and_exit_status_that_disagree_are_a_contract_violation() -> None:
    clean_but_failing = """
        import os
        open(os.environ["TRESTLE_TEST_REPORT"], "w").write(
            '<testsuite tests="1" failures="1" errors="0" skipped="0"/>')
    """
    _, result = _run(_command(clean_but_failing, reports_tests=True))  # exit 0, one failure
    assert result is not None
    assert result.classification is ports.ExecutionClass.CONTRACT_VIOLATION
    _, missing = _run(_command("pass", reports_tests=True))  # no report at all
    assert missing is not None
    assert missing.classification is ports.ExecutionClass.CONTRACT_VIOLATION
    assert missing.counts is None


def test_argv0_must_be_the_resolved_executable_and_a_missing_one_is_not_applied() -> None:
    wrong = ports.BoundCommand("t", ("/bin/echo", "x"), {}, RESOLVED, False)
    with pytest.raises(ValueError, match="argv"):
        _run(wrong)
    gone = ports.Resolved("/nonexistent/tool", "1", "p", "a")
    absent = ports.BoundCommand("t", ("/nonexistent/tool",), {}, gone, False)
    confirmation, result = _run(absent)
    assert confirmation.status is ConfirmationStatus.NOT_APPLIED and result is None
    assert confirmation.code == command_mod.TOOLCHAIN_MISSING
