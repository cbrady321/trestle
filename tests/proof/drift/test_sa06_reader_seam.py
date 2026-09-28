"""SA-06 drift proof: the proof harness reads a run purely through the
independent `tests.proof.records` seam, never through a product reader
(L.P0-0b.1)."""

from __future__ import annotations

import pytest

from tests.proof import harness, records


@pytest.mark.parametrize("sa", ["SA-06"])
def test_seam_reads_harness_run(sa: str) -> None:
    kernel = harness.fresh_kernel()
    run_dir = harness.run_to_dir(kernel, "echo", {"message": "sa-06"}, wait_ms=5000)

    rows = records.ledger_rows(run_dir)
    assert not rows.torn
    assert not rows.merged
    kinds = [row.get("kind") for row in rows.rows]
    assert kinds == [
        "created",
        "admitted",
        "started",
        "execution_ended",
        "evidence_finalized",
        "succeeded",
    ]

    node = records.node_record(run_dir)
    assert node.kinds == kinds
    assert node.terminal == "succeeded"
