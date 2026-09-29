"""G-A2 (BFD-03): enforcement uses a spawn-relative timer, not the
admitted deadline.

`Admission.admit` fixes `spec.deadline`; `Conductor.drive` ignores it and
grants the full `timeout_s` again from spawn, so a run driven after its
admitted deadline still succeeds.
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


@pytest.mark.pin("G-A2")
def test_pin_run_driven_after_admitted_deadline_succeeds() -> None:
    classification, terminal = _admit_hold_then_drive()
    assert classification == "succeeded"
    assert terminal == "succeeded"


@pytest.mark.target("G-A2")
@pytest.mark.proves(
    "WR-DEADLINE-2", "WR-DEADLINE-2:ends-in-deadline-window", "core", "core", "PROC", "CI"
)
@pytest.mark.xfail(strict=True, reason="defect:G-A2")
def test_target_run_driven_after_admitted_deadline_times_out() -> None:
    classification, terminal = _admit_hold_then_drive()
    target_check(
        classification == "timed_out" and terminal == "timed_out",
        "G-A2",
        f"run driven after its admitted deadline ended {classification!r}/{terminal!r}, "
        "not 'timed_out'",
    )
