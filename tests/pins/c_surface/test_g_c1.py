"""G-C1 (BFD-22/23), flipped by L.CS-4.1: a wait keyed to spec.deadline no longer returns
"running" for a run that times out at its budget.

`run(completion="terminal")` (MC-16) waits for the finalized terminal row, bounded by the run's
deadline plus `clock.finalization_margin`: at the budget edge the answer is terminal and the
terminal row already exists. (Bounded mode, with `wait_ms` equal to the budget, still answers a
`running` frame, byte-for-byte as before: WR-COMPAT-3.)
"""

from __future__ import annotations

import pytest

from tests.pins.c_surface._support import BUDGET_MS, BUDGET_S, as_view, run_dir, settle
from tests.proof import harness, records
from tests.proof.markers import target_check
from trestle.server.ledger import TERMINAL_KINDS

GAP = "G-C1"


def wait_at_budget() -> tuple[object, str, str | None, str]:
    """Admit `slow` with a whole-second budget, wait for its terminal answer (`wait_ms` is the
    budget, as a caller keying the wait to the deadline would send it), and report (view, run_id,
    ledger terminal at return, final terminal after settling)."""
    kernel = harness.fresh_kernel()
    with harness.patch_snapshot(kernel, "slow", timeout_s=BUDGET_S):
        view = as_view(kernel.control.run(plugin="slow", wait_ms=BUDGET_MS, completion="terminal"))
    terminal_at_return = records.node_record(run_dir(kernel, view.run_id)).terminal
    final = settle(kernel, view.run_id)
    return view, view.run_id, terminal_at_return, final.state


@pytest.mark.proves("WR-TERM-2", "A1.1:core", "A", "core", "PROC", "CI")
@pytest.mark.proves(
    "WR-TERM-2", "WR-TERM-2:budget-edge-never-running", "core", "core", "PROC", "CI"
)
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
