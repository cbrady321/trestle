"""Reading pytest's JUnit XML (written with `-o junit_family=xunit1`) into a bounded report."""

from __future__ import annotations

import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass, field
from pathlib import Path

# pytest's exit codes (`pytest.ExitCode`)
EXIT_MEANINGS = {
    0: "all tests passed",
    1: "tests failed",
    2: "interrupted (for example a collection error)",
    3: "internal error",
    4: "usage error",
    5: "no tests collected",
}
MESSAGE_CHARS = 300
DETAIL_CHARS = 1500


@dataclass
class Failure:
    """One failed or errored test: its node id, the kind (`failed` or `error`), the first line of
    the message, and the end of the traceback."""

    nodeid: str
    kind: str
    message: str
    detail: str


@dataclass
class PytestReport:
    """One pytest run's verdict. `ok` is exit code 0; a failing test is `ok: false` with the test
    in `failures` (at most `max_failures` of them, `failures_omitted` counts the rest). `error` is
    set when pytest gave no report (it could not start, or the run timed out)."""

    ok: bool
    exit_code: int | None
    summary: str
    collected: int = 0
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    duration_s: float = 0.0
    failures: list[Failure] = field(default_factory=list)
    failures_omitted: int = 0
    error: str | None = None
    python: str = ""


def _first_line(text: str) -> str:
    line = text.strip().splitlines()[0] if text.strip() else ""
    return line[:MESSAGE_CHARS]


def _nodeid(case: ElementTree.Element) -> str:
    """`path/to/test_mod.py::Class::name` from xunit1's `file` and dotted `classname`."""
    classname = case.get("classname", "")
    name = case.get("name", "")
    source = case.get("file")
    if source:
        module = source.removesuffix(".py").replace("/", ".").replace("\\", ".")
        rest = classname[len(module) :].lstrip(".") if classname.startswith(module) else ""
        return "::".join(p for p in (source, *rest.split(".")) if p) + f"::{name}"
    return f"{classname}::{name}" if classname else name


def read_junit_report(
    junit: Path,
    *,
    exit_code: int | None,
    duration_s: float = 0.0,
    max_failures: int = 20,
    python: str = "",
) -> PytestReport | None:
    """The report for the JUnit file pytest wrote, or None when there is none to read."""
    try:
        root = ElementTree.parse(junit).getroot()
    except (OSError, ElementTree.ParseError):
        return None
    # Counted per test case, as pytest reports them: a teardown error on a test whose call
    # passed is one pass and one error; a collection error is an error and no test.
    total = passed = failed = errors = skipped = 0
    found: list[Failure] = []
    for case in root.iter("testcase"):
        failures, problems = case.findall("failure"), case.findall("error")
        messages = [(p.get("message", "") or "") for p in problems]
        collection = any(m.startswith("collection failure") for m in messages)
        total += 0 if collection else 1
        errors += len(problems)
        if failures:
            failed += 1
        elif case.find("skipped") is not None:
            skipped += 1
        elif not collection and all(m.startswith("failed on teardown") for m in messages):
            passed += 1
        for kind, problem in [("failed", f) for f in failures] + [("error", e) for e in problems]:
            text = problem.text or ""
            found.append(
                Failure(
                    nodeid=_nodeid(case),
                    kind=kind,
                    message=_first_line(problem.get("message", "") or text),
                    detail=text.strip()[-DETAIL_CHARS:],
                )
            )
    meaning = EXIT_MEANINGS.get(exit_code, "") if exit_code is not None else "did not finish"
    summary = f"{passed} passed, {failed} failed, {errors} errors, {skipped} skipped"
    return PytestReport(
        ok=exit_code == 0,
        exit_code=exit_code,
        summary=f"{summary} ({meaning})" if meaning else summary,
        collected=total,
        passed=passed,
        failed=failed,
        errors=errors,
        skipped=skipped,
        duration_s=round(duration_s, 3),
        failures=found[:max_failures],
        failures_omitted=max(0, len(found) - max_failures),
        python=python,
    )
