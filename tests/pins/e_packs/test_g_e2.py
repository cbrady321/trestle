"""G-E2: the only live Docker test (`test_stack_runner_live_compose`) used to skip
when its image was not usable, and nothing reported it. Flipped by L.NW-2.8: the
image set is pinned (PX-3), the host-docker gate runs the live node inside the gate
(where a skip is FAILED), and the target reads a host-docker record valid for HEAD in
which that node PASSED. The today-pin (the skip renders UNPROVEN in the ledger) is
deleted with the flip, as G-E1 and G-E3's were."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.proof.host import record as record_mod
from tests.proof.markers import target_check

ROOT = Path(__file__).resolve().parents[3]
LIVE_FILE = "packages/trestle-packs/tests/test_docker_integration.py"
LIVE_NODE = f"{LIVE_FILE}::test_stack_runner_live_compose"
LABEL = "WR-PROOF-2:pack-docker-live"


@pytest.mark.proves("WR-PROOF-2", LABEL, "core", "core", "must", "CI")
def test_target_host_docker_record_passes_live_compose() -> None:
    head = record_mod.fence_mod._git(ROOT, "rev-parse", "HEAD").stdout.strip()  # noqa: SLF001
    record = record_mod.select("host-docker", head, cwd=ROOT)
    target_check(record is not None, "G-E2", "no host-docker record admissible for HEAD")
    assert record is not None
    target_check(
        record["mode"] == "run" and record["status"] == "PASSED",
        "G-E2",
        f"host-docker record for HEAD is {record['mode']}/{record['status']}, not a passing run",
    )
    passed = [
        r
        for r in record["results"]
        if r.get("nodeid") == LIVE_NODE and r.get("outcome") == "PASSED"
    ]
    target_check(bool(passed), "G-E2", "the live compose node is not PASSED in the record")
