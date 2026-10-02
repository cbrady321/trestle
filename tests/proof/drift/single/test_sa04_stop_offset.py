"""SA-04 in single (L.SV-5.12): the flag-then-offset stop protocol and one serialized appender,
proven from records with the plugin dead. The offset suite (`tests/proof/spine/test_stop_offset.py`)
reads a stopped run's directory after every process of the run has exited, through the proof
court's seam only, and catches a planted concurrent write."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from tests.core.spine import support
from tests.proof import records
from tests.proof.spine import test_stop_offset as suite

SUITE = Path(suite.__file__)


def _imported_trestle_modules() -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(SUITE.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
        elif isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
    return {name for name in found if name.split(".")[0] == "trestle"}


@pytest.mark.parametrize("sa", ["SA-04"])
def test_offset_suite_plugin_dead(sa: str, tmp_path: Path) -> None:
    run = suite.run_cancelled()

    # the plugin is dead: nothing of the run is alive, the record is all there is
    assert not support.marked(run.run_dir.name)

    # the suite reads records through the seam: it imports no product reader of the lane, the
    # fold, the child or the workflow package, only the published clock
    assert _imported_trestle_modules() <= {"trestle.common"}, _imported_trestle_modules()

    # the offset holds on the real record ...
    (stop,) = run.stops
    releases = suite.release_effects(run.run_dir)
    assert suite.offset_verdict(run.lane, [stop], releases).ok

    # ... and a second appender (a planted concurrent write) is caught, by its offset or its seq
    last = run.lane.rows[-1].entry["seq"]
    lane = suite._copy_lane(run, tmp_path, suite._late_apply(run, seq=last + 1))
    assert not suite.offset_verdict(lane, [stop], releases).ok
    clash = suite._copy_lane(run, tmp_path, suite._late_apply(run, seq=last - 1))
    assert clash.problems and not suite.offset_verdict(clash, [stop], releases).ok
    # the stop row is the ledger's, never the lane's: nothing U2 writes is in the lane (B2-C15)
    assert all(r.cls != "stop_row" for r in records.lane_rows(run.run_dir).rows)
    assert json.loads((run.run_dir / "evidence" / "spec.json").read_text("utf-8"))["plan"]
