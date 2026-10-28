"""Contract-violation classification of a toolchain task's outcome (L.RB-4.4; B3-C14, B3-E1,
B3-E5, V-11, V-13, WR-VERIFY-5, WR-EVID-7, WR-EVID-10).

A task speaks three ways: its exit status, the report it wrote (a JSON `{"outcome": "passed" |
"failed"}` or a JUnit XML file) and the artifacts it was declared to leave. `classify(result,
reported=..., report_expected=..., missing_artifacts=...)` returns the `ExecutionResult` whose
classification says what they agree on; it reads no console text, so a result is never inferred
from it (B3-C14: "a result is never inferred from console text"):

- exit 0, the report (if any) not failed, the report present when one is expected, every declared
  artifact present: `PASSED`;
- exit 0 but the report says failed, or a report was expected and is absent or unreadable, or a
  declared artifact is missing: `CONTRACT_VIOLATION` (the tool claims a success its own evidence
  denies);
- a non-zero exit while the report says passed: `CONTRACT_VIOLATION`;
- a non-zero exit otherwise (report failed or none): `FAILED` (artifacts a failed run left are
  not a disagreement);
- a result that is already `INTERRUPTED` (the port ended the process) is returned unchanged;
- `counts_required` (a test selector) with no counts: `CONTRACT_VIOLATION`, never `PASSED`.

`code` stays a V-11 code or `None` and this module invents none: it is `TOOLCHAIN_MISSING` only when
the caller reports `self_provisioned` (the task started and then reached for a toolchain, B3-E5;
`CONTRACT_VIOLATION`, never `PASSED`), and `None` for every other outcome. A node whose task is
classified `CONTRACT_VIOLATION` ends `FAILED` through J-6: recorded, not passed.

`bound_excerpt(text)` keeps the console evidence within `TEXT_MAX` (V-13; equal to MC-15's 512 B):
the whole text when it fits, else the marker `[truncated] ` followed by the newest text that fits,
never raised. `reproduction(argv, environment)` is the one command line that reruns a recorded
invocation by hand, argv and environment exactly as recorded (`env -i` first, so nothing else leaks
in): the by-hand run gives the same exit status, report and artifacts (WR-EVID-10).

The adapter imports only the standard library and `trestle.workflow` (BFD-47).
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ElementTree
from collections.abc import Mapping, Sequence
from dataclasses import replace
from enum import StrEnum
from pathlib import Path
from typing import Final

from trestle.workflow.ports import ExecutionClass, ExecutionResult

TOOLCHAIN_MISSING: Final = "execution.toolchain_missing"
TEXT_MAX: Final = 512  # V-13 TEXT_MAX (BoundedText); MC-15's message bound
TRUNCATION_MARKER: Final = "[truncated] "
ENV_PROGRAM: Final = "/usr/bin/env"


class Reported(StrEnum):
    """What a task's own report says."""

    PASSED = "passed"
    FAILED = "failed"


def read_report(path: str | Path) -> Reported | None:
    """The outcome a report file states, or `None` when it is absent or unreadable.

    JSON `{"outcome": "passed" | "failed"}`, or JUnit XML (`testsuite`/`testsuites`: any failure or
    error is `FAILED`, none is `PASSED`)."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None
    try:
        document = json.loads(text)
    except ValueError:
        return _junit(text)
    outcome = document.get("outcome") if isinstance(document, dict) else None
    return Reported(outcome) if outcome in {r.value for r in Reported} else None


def _junit(text: str) -> Reported | None:
    try:
        root = ElementTree.fromstring(text)
    except ElementTree.ParseError:
        return None
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    if not suites:
        return None
    try:
        bad = sum(int(s.get("failures", "0")) + int(s.get("errors", "0")) for s in suites)
    except ValueError:
        return None
    return Reported.FAILED if bad else Reported.PASSED


def bound_excerpt(text: str) -> str:
    """`text` within `TEXT_MAX` bytes: itself, or a truncation marker and its newest tail."""
    data = text.encode("utf-8")
    if len(data) <= TEXT_MAX:
        return text
    room = TEXT_MAX - len(TRUNCATION_MARKER.encode("utf-8"))
    return TRUNCATION_MARKER + data[-room:].decode("utf-8", "ignore")


def classify(
    result: ExecutionResult,
    *,
    reported: Reported | None = None,
    report_expected: bool = False,
    missing_artifacts: Sequence[str] = (),
    counts_required: bool = False,
    self_provisioned: bool = False,
) -> ExecutionResult:
    """The result with the classification the task's exit status, report and artifacts agree on."""
    klass = ExecutionClass(getattr(result.classification, "value", result.classification))
    if klass is ExecutionClass.INTERRUPTED:
        return result
    ok = result.exit_status == 0
    code: str | None = None
    if self_provisioned:
        verdict = ExecutionClass.CONTRACT_VIOLATION
        code = TOOLCHAIN_MISSING
    elif ok:
        disagrees = (
            reported is Reported.FAILED
            or (report_expected and reported is None)
            or bool(missing_artifacts)
            or (counts_required and result.counts is None)
        )
        verdict = ExecutionClass.CONTRACT_VIOLATION if disagrees else ExecutionClass.PASSED
    elif reported is Reported.PASSED:
        verdict = ExecutionClass.CONTRACT_VIOLATION
    else:
        verdict = ExecutionClass.FAILED
    return replace(
        result, classification=verdict, code=code, excerpt=bound_excerpt(str(result.excerpt))
    )


def reproduction(argv: Sequence[str], environment: Mapping[str, str]) -> tuple[str, ...]:
    """The command line that reruns a recorded invocation by hand: `env -i K=V ... argv`."""
    return (ENV_PROGRAM, "-i", *(f"{k}={v}" for k, v in sorted(environment.items())), *argv)
