"""pytest_run: run a project's tests on the project's own interpreter and report the result.

The recipe, top to bottom (adapt it by copying this file and publishing it under a new name):

1. find the project's interpreter (`python`, else `.venv` or `venv` in `workdir` or a parent up
   to the repository root; `Blocked` when there is none);
2. run `<python> -m pytest <path> -q --tb=short -p no:cacheprovider --junitxml=<file>
   -o junit_family=xunit1 [-m markers] [-k keyword] [pytest_args...]` with `ctx.exec`, in
   `workdir`, with PYTHONPATH unset so nothing of the Trestle service leaks in;
3. read the counts and failures from the JUnit file, never from console text;
4. attach the JUnit file and the JSON report; answer the report.

A failing check raises `Failed` (see `fail_for`), carrying the report as its result, so the run
answers `outcome: failed` and the summary still shows `ok: false` and each test in `failures`.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from trestle_pack_pytest import find_project_python, read_junit_report
from trestle_pack_pytest.junit import PytestReport  # where it is defined: the schema reads it

from trestle.plugin import Blocked, Context, Failed, trestle


def fail_for(report: PytestReport) -> Failed:
    """The `Failed` that carries a not-ok report: `tests_failed` for failing or erroring tests,
    `tests_incomplete` when pytest gave no result (timed out, stopped, no JUnit report) and
    `no_tests_collected` when it found nothing to run."""
    if report.error:
        return Failed("tests_incomplete", f"pytest gave no result: {report.error[:300]}", report)
    if report.exit_code == 5:
        return Failed("no_tests_collected", "pytest collected no tests", report)
    bad = report.failed + report.errors
    detail = (
        f"{report.failed} of {report.collected} tests failed"
        + (f", {report.errors} errored" if report.errors else "")
        if bad
        else f"pytest exited {report.exit_code}: {report.summary}"
    )
    return Failed("tests_failed", detail, report)


@trestle(packages=["trestle_pack_pytest"], summary_fields=["ok", "summary", "failures"])
def pytest_run(
    ctx: Context,
    path: str = ".",
    markers: str | None = None,
    keyword: str | None = None,
    workdir: str | None = None,
    python: str | None = None,
    pytest_args: list[str] | None = None,
    timeout_s: float | None = None,
    max_failures: int = 20,
) -> PytestReport:
    """Run pytest out of process on the project's interpreter.

    `path` is a test path or node id, relative to `workdir` (the project root; default the
    current directory). `python` names the interpreter when the project has no `.venv` or
    `venv`. `pytest_args` are passed through. Failing tests raise `Failed` (`tests_failed`; also
    `tests_incomplete` and `no_tests_collected`) with the report as the result: `ok: false` and
    each failure's node id, message and traceback tail. The JUnit XML and a JSON report are
    attached.
    """
    root = Path(workdir) if workdir else Path.cwd()
    interpreter = find_project_python(root, python)
    junit = ctx.tmp / "pytest-junit.xml"
    junit.unlink(missing_ok=True)
    argv = [interpreter, "-m", "pytest", path, "-q", "--tb=short", "-p", "no:cacheprovider"]
    argv += [f"--junitxml={junit}", "-o", "junit_family=xunit1"]
    if markers:
        argv += ["-m", markers]
    if keyword:
        argv += ["-k", keyword]
    argv += pytest_args or []

    ctx.log(f"pytest: {path} in {root} on {interpreter}")
    done = ctx.exec(
        argv, cwd=root, timeout=timeout_s, env={"PYTHONPATH": None}, output="pytest-output.log"
    )
    if "No module named pytest" in done.stderr:
        raise Blocked(
            "pytest_missing",
            f"pytest is not installed in the project's interpreter {interpreter}. Install it "
            f"(`{interpreter} -m pip install pytest`, or the project's test extras), then resend.",
        )

    report = read_junit_report(
        junit,
        exit_code=done.returncode,
        duration_s=done.duration_s,
        max_failures=max_failures,
        python=interpreter,
    )
    if report is None or done.timed_out or done.cancelled:
        why = "timed out" if done.timed_out else "stopped" if done.cancelled else "no JUnit report"
        report = report or PytestReport(
            ok=False, exit_code=done.returncode, summary=f"pytest gave no result ({why})",
            python=interpreter,
        )  # fmt: skip
        report.ok = False
        report.error = f"{why}: {(done.stderr or done.stdout).strip()[-1500:]}"
    if junit.exists():
        ctx.attach(junit, name="pytest-junit.xml")
    staged = ctx.artifact("pytest-report.json")
    staged.write_text(json.dumps(asdict(report), indent=2), encoding="utf-8")
    ctx.attach(staged, name="pytest-report.json")
    ctx.log(f"pytest: {report.summary}")
    if not report.ok:
        raise fail_for(report)
    return report
