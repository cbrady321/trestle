"""G-A1 (BFD-01): a detached tree survives cancel and timeout.

`terminate_process_group` signals only the wrapper's process group; the
child (own session, `spawn_child`'s `start_new_session`) and a setsid
grandchild are outside it, so both survive the terminal row.
"""

from __future__ import annotations

import pytest

from tests.pins.a_lifecycle import helpers
from tests.proof import harness, tolerances
from tests.proof.markers import target_check
from trestle.server import runs


def _end_and_observe(kernel, ending: str, monkeypatch: pytest.MonkeyPatch):
    """Drive a `detach` run to its terminal row by `ending` ('cancel' or
    'timeout') and return (tree observed live, terminal, survivors after)."""
    monkeypatch.setattr(runs, "CANCEL_GRACE_S", tolerances.SETTLE_SHORT_S)
    monkeypatch.setattr(runs, "CANCEL_KILL_S", tolerances.SETTLE_LONG_S)
    timeout = helpers.SHORT_RUN_TIMEOUT_S if ending == "timeout" else None
    if timeout is None:
        order = helpers.admit_order(kernel, "detach")
        thread = helpers.drive_in_thread(kernel, order)
    else:
        with harness.patch_snapshot(kernel, "detach", timeout_s=timeout):
            order = helpers.admit_order(kernel, "detach")
        thread = helpers.drive_in_thread(kernel, order)
    run_id = order.run_id
    with helpers.reaping(run_id):
        tree = helpers.observe_live_tree(kernel, run_id)
        if ending == "cancel":
            kernel.control.cancel(run_id)
        thread.join(timeout=tolerances.JOIN_WAIT_S + helpers.SHORT_RUN_TIMEOUT_S)
        assert not thread.is_alive(), "conductor never returned"
        run_dir = helpers.run_dir_of(kernel, run_id)
        terminal = helpers.terminal_of(run_dir)
        # Give a correct product its own bounded settle before reading.
        helpers.wait_until(
            lambda: not helpers.alive_marked(tree.live, run_id), tolerances.PROC_WAIT_S
        )
        return tree, terminal, helpers.alive_marked(tree.live, run_id)


@pytest.mark.pin("G-A1")
def test_pin_tree_survives_cancel_and_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    for ending, expected in (("cancel", "cancelled"), ("timeout", "timed_out")):
        kernel = helpers.lane_kernel()
        tree, terminal, survivors = _end_and_observe(kernel, ending, monkeypatch)
        assert terminal == expected, (ending, terminal)
        assert tree.grandchild <= survivors, f"{ending}: setsid grandchild did not survive"
        assert tree.child <= survivors, f"{ending}: session-leader child did not survive"


@pytest.mark.target("G-A1")
@pytest.mark.proves("A6.1", "A6.1:core", "A", "core", "PROC", "BOTH")
@pytest.mark.proves(
    "WR-CANCEL-1", "WR-CANCEL-1:cancel-tree-within-bound", "core", "core", "PROC", "BOTH"
)
@pytest.mark.xfail(strict=True, reason="defect:G-A1")
def test_target_no_attributable_survivor_after_terminal_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for ending in ("cancel", "timeout"):
        kernel = helpers.lane_kernel()
        _tree, _terminal, survivors = _end_and_observe(kernel, ending, monkeypatch)
        target_check(
            not survivors,
            "G-A1",
            f"{ending}: {len(survivors)} attributable process(es) alive after the terminal row",
        )
