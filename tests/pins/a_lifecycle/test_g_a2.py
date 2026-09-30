"""G-A2 (BFD-03), flipped by L.CS-2.3: the admitted deadline is the one enforced.

`Admission.admit` fixes `spec.deadline` and `Conductor.drive` measures the run's monotonic
deadline from it, not from spawn, so a run driven after its admitted deadline ends timed out.
"""

from __future__ import annotations

import time

import pytest

from tests.pins.a_lifecycle import helpers
from tests.proof import harness, tolerances
from tests.proof.markers import target_check


def _admit_hold_then_drive() -> tuple[str, str | None]:
    """Admit `echo` with a short deadline, hold it past `spec.deadline`, then
    drive it. Returns (classification `drive` returned, ledger terminal)."""
    kernel = helpers.lane_kernel()
    with harness.patch_snapshot(kernel, "echo", timeout_s=helpers.SHORT_RUN_TIMEOUT_S):
        order = helpers.admit_order(kernel, "echo", {"message": "g-a2"})
    time.sleep(helpers.SHORT_RUN_TIMEOUT_S + tolerances.SETTLE_SHORT_S)
    classification = kernel.control.conductor.drive(order)
    run_dir = helpers.run_dir_of(kernel, order.run_id)
    return classification, helpers.terminal_of(run_dir)


@pytest.mark.proves(
    "WR-DEADLINE-2", "WR-DEADLINE-2:ends-in-deadline-window", "core", "core", "PROC", "CI"
)
def test_target_run_driven_after_admitted_deadline_times_out() -> None:
    classification, terminal = _admit_hold_then_drive()
    target_check(
        classification == "timed_out" and terminal == "timed_out",
        "G-A2",
        f"run driven after its admitted deadline ended {classification!r}/{terminal!r}, "
        "not 'timed_out'",
    )
