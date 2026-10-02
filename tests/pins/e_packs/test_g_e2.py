"""G-E2: the only live Docker test (`test_stack_runner_live_compose`) used to skip
when its image was not usable, and nothing reported it. Flipped by L.NW-2.8: the
image set is pinned (PX-3), the host-docker gate runs the live node inside the gate
(where a skip is FAILED), and the target reads a host-docker record valid for HEAD in
which that node PASSED. The today-pin (the skip renders UNPROVEN in the ledger) is
deleted with the flip, as G-E1 and G-E3's were. On a pull_request run a head with no record yet
is pending (skipped), not failing: the owner decision of 2026-10-02 (`record.host_docker_pending`);
landing still requires the record (fence rule L1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.proof.host import record as record_mod
from tests.proof.markers import target_check

ROOT = Path(__file__).resolve().parents[3]
LABEL = "WR-PROOF-2:pack-docker-live"


@pytest.mark.proves("WR-PROOF-2", LABEL, "core", "core", "must", "CI")
def test_target_host_docker_record_passes_live_compose() -> None:
    head = record_mod.fence_mod._git(ROOT, "rev-parse", "HEAD").stdout.strip()  # noqa: SLF001
    problem = record_mod.host_docker_problem(head, cwd=ROOT)
    if record_mod.host_docker_pending(problem):
        # a PR head before its host session: pending, not failing (owner decision 2026-10-02);
        # the fence's landing rule L1 still refuses a landing without the record
        pytest.skip(f"G-E2 pending on a pull_request run: {problem}")
    target_check(problem is None, "G-E2", str(problem))
