"""The live spine-budget check (L.CZ.8; J-ROOT step 3, HYBRID risk 7).

`check_spine_gate_within_budget(junit_path)` reads the `spine` CI job's junit file and fails when
the gate's own duration exceeds `SPINE_BUDGET_S` (15 minutes on 3.12 CI). This module is named
neither `test_*.py` nor `*_test.py`, so it is never default-collected (DM-80): `root.py` runs it
with the spine job's junit (`tests/proof/results/spine-junit.xml`, uploaded with that job's
results), and its planted self-test is `test_spine_budget.py::test_planted_slow_junit_fails`, which
is true on every head.

    python -m tests.proof.selftest.spine_budget_check --junit <spine job junit>
"""

from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

SPINE_BUDGET_S = 15 * 60  # the `spine` job's junit duration bound on 3.12 CI


def junit_duration_s(junit_path: Path) -> float:
    """The gate's duration: a `testsuites` root's own `time`, else the sum of its `testsuite`
    children's, else a lone `testsuite` root's. Raises `ValueError` on an unreadable file or one
    that carries no time at all (a budget cannot be judged from nothing)."""
    try:
        root = ET.parse(junit_path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise ValueError(f"{junit_path}: not a readable junit file ({exc})") from exc
    if root.tag == "testsuite":
        suites = [root]
    elif root.get("time") is not None:
        return float(str(root.get("time")))
    else:
        suites = list(root.iter("testsuite"))
    times = [float(str(s.get("time"))) for s in suites if s.get("time") is not None]
    if not times:
        raise ValueError(f"{junit_path}: no testsuite carries a time")
    return sum(times)


def check_spine_gate_within_budget(
    junit_path: Path | str, budget_s: float = SPINE_BUDGET_S
) -> tuple[bool, str]:
    """`(ok, message)`: ok iff the spine gate's junit duration is within `budget_s`."""
    try:
        duration = junit_duration_s(Path(junit_path))
    except ValueError as exc:
        return False, str(exc)
    if duration > budget_s:
        return False, f"spine gate took {duration:.0f} s, over the {budget_s:.0f} s budget"
    return True, f"spine gate took {duration:.0f} s, within the {budget_s:.0f} s budget"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tests.proof.selftest.spine_budget_check")
    parser.add_argument("--junit", required=True, help="the spine job's junit xml")
    args = parser.parse_args(argv)
    ok, message = check_spine_gate_within_budget(args.junit)
    print(f"spine budget: {message}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
