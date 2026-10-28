"""A unit blocked in `wait_for` wakes on the in-process goal flip (DEFERRED-DECISIONS Q1, the
fail-fast latency proof): when a sibling raises, the waiting unit's wait returns `interrupted`
within the settle bound, the waiter ends `cut=STOPPED`, and nothing non-release follows the raise.
Times are read from the lane's `at` stamps, never from a pause."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from tests.core.spine import support
from tests.proof import harness, records, tolerances
from tests.tree import treekit as tk
from trestle.server.ledger import evidence_dir
from trestle.server.main import Kernel


def _published(tmp_path: Path, name: str) -> Path:
    path = tmp_path / "plugins" / f"{name}.py"
    path.write_text(tk.fixture_source(name), encoding="utf-8")
    return path


def _unit_events(run_dir: Path, kind: str) -> list[dict[str, object]]:
    lines = (evidence_dir(run_dir) / "events.ndjson").read_text(encoding="utf-8").splitlines()
    return [e["payload"] for e in map(json.loads, lines) if e["kind"] == kind]


def test_a_waiting_unit_wakes_when_a_sibling_raises(tree_kernel: Kernel, tmp_path: Path) -> None:
    path = _published(tmp_path, "wait_interface")
    admitted = harness.admit_tree(path, {"env": "dev"}, kernel=tree_kernel)
    with support.reaping(admitted.run_id):
        harness.drive_tree(admitted)
    assert _unit_events(admitted.run_dir, "wait.ended") == [{"why": "interrupted"}]
    lane = records.lane_rows(admitted.run_dir)
    assert not lane.problems and not lane.torn, lane.problems
    raised = next(
        r for r in lane.rows if r.cls == "step" and r.entry["code"] == "execution.unit_raised"
    )
    assert raised.path == "raiser"
    ends = tk.ends_by_path(admitted.run_dir)
    assert ends["waiter"]["cut"] == "stopped"
    woke = datetime.fromisoformat(str(ends["waiter"]["at"]))
    flipped = datetime.fromisoformat(raised.entry["at"])
    assert (woke - flipped).total_seconds() < tolerances.SETTLE_S, (flipped, woke)
    after = lane.rows[lane.rows.index(raised) + 1 :]
    assert not [(r.path, r.cls) for r in after if r.cls == "issue" and r.entry["effect"] != "stop"]
