"""G-C1 (BFD-22/23): a wait keyed to spec.deadline returns "running" for a
run that times out at its budget.

Pin: `run(wait_ms=<the run's own budget>)` returns a `running` frame with no
terminal row yet. Target (written on today's `wait_ms`; CS-4 re-points it to
MC-16): the answer is terminal and the terminal row already exists.
"""

from __future__ import annotations

import pytest

from tests.pins.c_surface._support import BUDGET_MS, BUDGET_S, as_view, run_dir, settle
from tests.proof import harness, records
from tests.proof.markers import target_check
from trestle.server.ledger import TERMINAL_KINDS

GAP = "G-C1"


def wait_at_budget() -> tuple[object, str, str | None, str]:
    """Admit `slow` with a whole-second budget, wait for exactly that
    budget, and report (view, run_id, ledger terminal at return, final
    terminal after settling)."""
    kernel = harness.fresh_kernel()
    with harness.patch_snapshot(kernel, "slow", timeout_s=BUDGET_S):
        view = as_view(kernel.control.run(plugin="slow", wait_ms=BUDGET_MS))
    terminal_at_return = records.node_record(run_dir(kernel, view.run_id)).terminal
    final = settle(kernel, view.run_id)
    return view, view.run_id, terminal_at_return, final.state


@pytest.mark.pin(GAP)
def test_pin_deadline_keyed_wait_returns_running() -> None:
    view, _run_id, terminal_at_return, final_state = wait_at_budget()
    assert view.state == "running"  # type: ignore[attr-defined]
    assert terminal_at_return is None
    assert final_state in TERMINAL_KINDS  # the run does time out afterwards


@pytest.mark.target(GAP)
@pytest.mark.proves("WR-TERM-2", "A1.1:core", "A", "core", "PROC", "CI")
@pytest.mark.proves(
    "WR-TERM-2", "WR-TERM-2:budget-edge-never-running", "core", "core", "PROC", "CI"
)
@pytest.mark.xfail(strict=True, reason="defect:G-C1")
def test_target_answer_terminal_and_after_terminal_row() -> None:
    view, _run_id, terminal_at_return, _final = wait_at_budget()
    state = view.state  # type: ignore[attr-defined]
    target_check(
        state in TERMINAL_KINDS, GAP, f"answer at the budget edge is {state!r}, not terminal"
    )
    target_check(
        terminal_at_return in TERMINAL_KINDS,
        GAP,
        "answer returned before the terminal ledger row",
    )
