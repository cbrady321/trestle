"""SA-06 in core (L.CS-2.1, .6): the proof court reads the core ledger kinds through the
independent `tests.proof.records` seam alone. `process_identity` (MC-14) and `group_stop` (MC-32)
are more kinds on that seam, each in its place in the row order, and never displace the S0 kinds
around them."""

from __future__ import annotations

import pytest

from tests.proof import harness, records, tolerances

S0_KINDS = [
    "created",
    "admitted",
    "started",
    "execution_ended",
    "evidence_finalized",
    "succeeded",
]
CORE_KINDS = {"process_identity", "group_stop"}


@pytest.mark.parametrize("sa", ["SA-06"])
def test_seam_reads_process_identity_in_row_order(sa: str) -> None:
    kernel = harness.fresh_kernel()
    run_dir = harness.run_to_dir(
        kernel, "echo", {"message": "sa-06-core"}, wait_ms=tolerances.HARNESS_WAIT_MS
    )

    oracle = records.ledger_rows(run_dir)
    assert not oracle.torn and not oracle.merged
    kinds = [row["kind"] for row in oracle.rows]
    assert records.node_record(run_dir).kinds == kinds
    # the S0 kinds keep their order around the core kinds
    assert [k for k in kinds if k not in CORE_KINDS] == S0_KINDS
    # the leader's identity row is the first row after `started` (B2-C16)
    after_started = kinds[kinds.index("started") + 1]
    assert after_started == "process_identity"
    leaders = [row for row in oracle.rows if row["kind"] == "process_identity" and row["leader"]]
    assert len(leaders) == 1 and oracle.rows[kinds.index("started") + 1] is leaders[0]
    # every identity row, then the one `group_stop` (after the kill), precede `evidence_finalized`
    finalized = kinds.index("evidence_finalized")
    assert kinds.count("group_stop") == 1
    stop_at = kinds.index("group_stop")
    assert stop_at < finalized
    assert all(i < stop_at for i, k in enumerate(kinds) if k == "process_identity")
