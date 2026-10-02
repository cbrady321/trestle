"""SA-06 in single (L.SV-5.10): the run directory the harness's `run_tree` leaves for a workflow
fixture is read by the proof court purely through the independent seam (`tests.proof.records`:
the ledger reader, the node-record reader and the strict lane oracle), never through a product
reader; the core kinds and the lane are additive to what the seam read of a plain plugin."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.proof import harness, records

FIXTURE = Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "workflows" / "spine_leaf.py"
# kinds the core and single phases add around the S0 sequence (MC-14, MC-19, B2-C7)
ADDITIVE_KINDS = {"process_identity", "group_stop", "lane_folded"}
S0_KINDS = ["created", "admitted", "started", "execution_ended", "evidence_finalized", "succeeded"]


@pytest.mark.parametrize("sa", ["SA-06"])
def test_harness_dir_readable_by_seam(sa: str) -> None:
    run_dir = harness.run_tree(FIXTURE, {"env": "dev", "mode": "advance"})

    rows = records.ledger_rows(run_dir)
    assert not rows.torn and not rows.merged
    kinds = [row.get("kind") for row in rows.rows]
    assert [k for k in kinds if k not in ADDITIVE_KINDS] == S0_KINDS

    node = records.node_record(run_dir)
    assert [k for k in node.kinds if k not in ADDITIVE_KINDS] == S0_KINDS
    assert node.terminal == "succeeded"

    lane = records.lane_rows(run_dir)
    assert lane.format == 1 and not lane.problems and not lane.torn and lane.unknown == 0
    assert [row.cls for row in lane.rows][0] == "plan"
    # the host folded every committed entry into the ledger, one row each (B2-C7)
    assert kinds.count("lane_folded") == len(lane.rows)
