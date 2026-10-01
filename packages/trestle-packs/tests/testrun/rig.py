"""Bindings of the test-run family (L.RB-5.1): the stdlib fake, and the real `PytestJunitRunner`
over the real `CommandPort` running real pytest (and a few one-line reporters) as real children.

The family's cases (`tests/conformance/testrun_cases.py`) run UNMODIFIED against both; what they
need beyond the port is the fixture contract in that module's docstring, built here. Nothing here
touches Docker: the real binding drives the host's own Python interpreter (OQ-23), so it is also
run through `host-proc` (`PROC`, venue BOTH).
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from textwrap import dedent

from conformance import testrun_cases as cases
from tests.proof.suites.ports import core
from tests.proof.suites.ports.implementations import LONG_RUN_S
from trestle.workflow import ports

from trestle_packs.fakes import ExecutionResult, TestCounts, passed_result
from trestle_packs.fakes.command import ExecutionClass
from trestle_packs.fakes.testrun import FakeTestRun
from trestle_packs.process.command import CommandPort
from trestle_packs.testrun import PytestJunitRunner

PROJECT_FILES = {
    "pytest.ini": "[pytest]\n",
    "test_passing.py": dedent(
        """
        import pytest

        def test_a(): pass
        def test_b(): pass
        def test_skipped(): pytest.skip("skipped by the suite")
        """
    ),
    "test_failing.py": dedent(
        """
        def test_ok(): pass
        def test_bad_one(): assert 1 == 2
        def test_bad_two(): assert False
        """
    ),
    "test_setup_error.py": dedent(
        """
        import pytest

        @pytest.fixture
        def broken():
            raise RuntimeError("setup fails")

        def test_needs_broken_fixture(broken): pass
        def test_fine(): pass
        """
    ),
    "test_teardown_error.py": dedent(
        """
        import pytest

        @pytest.fixture
        def leaky():
            yield
            raise RuntimeError("teardown fails")

        def test_body_passes(leaky): pass
        def test_fine(): pass
        """
    ),
    "test_collection_error.py": "import a_module_that_does_not_exist_anywhere\n",
}
REAL_SELECTORS = {
    "passing": "test_passing.py",
    "failing": "test_failing.py",
    "setup_error": "test_setup_error.py",
    "teardown_error": "test_teardown_error.py",
    "collection_error": "test_collection_error.py",
}

# One-line "test selectors" that write (or fail to write) the JUnit artifact the runner names with
# `--junitxml=<path>`: they stand for a selector whose report is missing, wrong, or huge.
_REPORT_PATH = "[a.split('=', 1)[1] for a in sys.argv if a.startswith('--junitxml=')][0]"
_ONE_PASS = (
    '<testsuite tests="1" failures="0" errors="0" skipped="0">'
    '<testcase classname="c" name="n"/></testsuite>'
)
REPORTERS = {
    "console_lies": "print('===== 5 passed in 0.01s =====')",  # no report at all
    "exit_disagrees": f"import sys; open({_REPORT_PATH}, 'w').write({_ONE_PASS!r}); sys.exit(1)",
    "chatty": (
        "import sys\n"
        f"print({cases.HEAD!r})\n"
        "for i in range(400): print('output line', i)\n"
        f"print({cases.TAIL!r})\n"
        f"open({_REPORT_PATH}, 'w').write({_ONE_PASS!r})\n"
    ),
    "long": f"import time; time.sleep({LONG_RUN_S})",
}


def _command(task: str, executable: str, argv: tuple[str, ...]) -> ports.BoundCommand:
    resolved = ports.Resolved(executable, "3.12", "pin", "adoption")
    return ports.BoundCommand(
        task, (executable, *argv), {"PYTHONDONTWRITEBYTECODE": "1"}, resolved, reports_tests=True
    )


def real_tasks(project: Path) -> dict[str, ports.BoundCommand]:
    project.mkdir(parents=True, exist_ok=True)
    for name, text in PROJECT_FILES.items():
        (project / name).write_text(text, encoding="utf-8")
    pytest_argv = ("-m", "pytest", "-p", "no:cacheprovider", "-c", str(project / "pytest.ini"))
    tasks = {
        task: _command(
            task, sys.executable, (*pytest_argv, "--rootdir", str(project), str(project / name))
        )
        for task, name in REAL_SELECTORS.items()
    }
    for task, program in REPORTERS.items():
        tasks[task] = _command(task, sys.executable, ("-c", "import sys\n" + program))
    return tasks


def real_pytest_junit(base: Path) -> Callable[[], core.Implementation]:
    tasks = real_tasks(base / "project")
    artifacts = base / "artifacts"

    def build() -> core.Implementation:
        runner = PytestJunitRunner(CommandPort(), artifacts)
        return core.Implementation(runner, name="pytest-junit", extras={"tasks": tasks})

    return build


def _scripted(task: str, expected: cases.Expected) -> ExecutionResult:
    counts = TestCounts(expected.passed, expected.failed, expected.errors, expected.skipped)
    if expected.failed or expected.errors:
        return ExecutionResult(1, ExecutionClass.FAILED, counts, expected.failing, None, "")
    return passed_result(counts)


def fake_tasks() -> dict[str, ports.BoundCommand]:
    return {
        task: _command(task, "/suite/bin/python", ("-m", "pytest", task))
        for task in (*cases.EXPECTED, "console_lies", "exit_disagrees", "long")
    }


def fake_testrun(fake_class: type[FakeTestRun] = FakeTestRun) -> Callable[[], core.Implementation]:
    def build() -> core.Implementation:
        results = {task: _scripted(task, want) for task, want in cases.EXPECTED.items()}
        results["console_lies"] = passed_result()  # no counts: the fake classifies it
        results["exit_disagrees"] = ExecutionResult(
            1, ExecutionClass.CONTRACT_VIOLATION, TestCounts(1, 0, 0, 0), (), None, ""
        )
        results["long"] = passed_result(TestCounts(1, 0, 0, 0))
        chatty = "\n".join([cases.HEAD, *(f"output line {i}" for i in range(400)), cases.TAIL, ""])
        impl = fake_class(results, {"chatty": chatty})
        return core.Implementation(impl, name="fake", extras={"tasks": fake_tasks()})

    return build
