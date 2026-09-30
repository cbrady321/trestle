"""Helpers for the TR-3 loop cases: a fixture made runnable, and the lane read back (L.TR-3.1).

A structural fixture (MC-B3-01) is declaration only and its function returns at once, so no
fixture runs a tree by itself. `runnable_source` rewrites the one entry function of a fixture (or
a generated tree) to make the loop's one call, `run_tree(ctx, ENTRY, {})` (B1-C9), which is the
only difference between the published source and the source a run executes."""

from __future__ import annotations

import re
from pathlib import Path

from tests.proof import records

REPO = Path(__file__).resolve().parents[2]
TREES = REPO / "tests" / "fixtures" / "trees"

_RETURN = re.compile(r"^    return \{.*\}\n\Z", re.MULTILINE)


def runnable_source(source: str) -> str:
    """`source` with its entry function's body replaced by the loop's one call."""
    body, count = _RETURN.subn("    run_tree(ctx, ENTRY, {})\n", source)
    assert count == 1, "the fixture's entry function is not the expected one-line return"
    anchor = "from trestle.plugin import"
    assert anchor in body
    return body.replace(anchor, "from trestle.workflow.loop import run_tree\n" + anchor, 1)


def fixture_source(name: str) -> str:
    return (TREES / f"{name}.py").read_text(encoding="utf-8")


def ends_by_path(run_dir: Path) -> dict[str, dict[str, object]]:
    """Every `NodeEnd` of the run's lane, keyed by the path text (`""` is the root)."""
    lane = records.lane_rows(run_dir)
    assert not lane.problems and not lane.torn, lane.problems
    ends: dict[str, dict[str, object]] = {}
    for row in lane.rows:
        if row.cls == "end":
            assert row.path is not None and row.path not in ends, row.path
            ends[row.path] = row.entry
    return ends
