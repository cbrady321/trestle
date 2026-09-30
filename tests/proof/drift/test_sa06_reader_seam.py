"""SA-06 drift proof: the proof harness reads a run purely through the
independent `tests.proof.records` seam, never through a product reader
(L.P0-0b.1)."""

from __future__ import annotations

import pytest

from tests.proof import harness, records, tolerances


@pytest.mark.parametrize("sa", ["SA-06"])
def test_seam_reads_harness_run(sa: str) -> None:
    kernel = harness.fresh_kernel()
    run_dir = harness.run_to_dir(
        kernel, "echo", {"message": "sa-06"}, wait_ms=tolerances.HARNESS_WAIT_MS
    )

    rows = records.ledger_rows(run_dir)
    assert not rows.torn
    assert not rows.merged
    kinds = [row.get("kind") for row in rows.rows]
    # core ledger kinds (process identity, MC-14) are additive: the seam sees the S0 kinds around
    # them unchanged, and tests/proof/drift/core/test_sa06_core_kinds.py holds their own claims
    kinds = [kind for kind in kinds if kind not in {"process_identity", "group_stop"}]
    assert kinds == [
        "created",
        "admitted",
        "started",
        "execution_ended",
        "evidence_finalized",
        "succeeded",
    ]

    node = records.node_record(run_dir)
    assert [k for k in node.kinds if k not in {"process_identity", "group_stop"}] == kinds
    assert node.terminal == "succeeded"
