"""CL-B3 (L.CL-B3.1; WR-OWN-6, WR-CANCEL-3, MC-32, B4-C7): the cleanup disposition sits beside the
primary outcome and never replaces or changes it; it is relayed, never adjudicated.

* A stop whose confirmation fails (an injected `Conductor.stopper` reporting `confirmed_gone=False`)
  leaves the run's state and `outcome` class exactly as a confirmed stop gives them, and projects
  `cleanup.processes == unknown` for a pass, a cancel and a deadline (never a clean answer).
* The reported disposition agrees with the process table (MC-13): a group gone at exit and a
  stopped group project `released` while the table holds no process of the run; an unconfirmed stop
  over a survivor projects `unknown` while the table still holds it. A spawned run never reads
  `nothing_created`.

Every timing bound comes from `tests.proof.tolerances` or `trestle.common.clock` (SA-05); no timing
literal appears here.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from tests.core.spine import support
from tests.proof import ancestry, harness, tolerances
from trestle.common import clock
from trestle.common.types import RunView
from trestle.server import procident
from trestle.server.procident import Attribution, GroupStop

HOLD_S = tolerances.JOIN_WAIT_S * 6


@pytest.fixture(autouse=True)
def short_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(clock, "grace", support.TEST_GRACE_S)
    monkeypatch.setattr(clock, "kill", support.TEST_KILL_S)


def unconfirmed_after_real_stop(attribution: Attribution) -> GroupStop:
    """The real stopper runs (so nothing outlives the test), then its confirmation is withheld:
    the host could not observe the group gone."""
    real = procident.stop_group(attribution)
    return GroupStop(confirmed_gone=False, signalled=real.signalled)


def unconfirmed_without_stopping(attribution: Attribution) -> GroupStop:
    """A stopper that stops nothing and says so: whatever the plugin left is still alive."""
    return GroupStop(confirmed_gone=False, signalled=False)


@dataclass(frozen=True)
class Case:
    label: str
    plugin: str
    args: dict[str, Any]
    state: str
    klass: str
    cancel: bool = False
    deadline: bool = False


CASES = (
    Case("passed", "echo", {"message": "b3"}, "succeeded", "passed"),
    Case("cancelled", "tree", {"seconds": HOLD_S}, "cancelled", "cancelled", cancel=True),
    Case("timed_out", "slow", {"seconds": HOLD_S}, "timed_out", "timed_out", deadline=True),
)


@dataclass(frozen=True)
class Finished:
    run_dir: Path
    view: RunView


def drive(stopper: Any, case: Case) -> Finished:
    """One run of `case` to a terminal state through the real control surface, over a kernel whose
    conductor uses `stopper` (None: the real one)."""
    kernel = support.spine_kernel()
    if stopper is not None:
        kernel.control.conductor.stopper = stopper
    if case.deadline:
        with harness.patch_snapshot(kernel, case.plugin, timeout_s=support.SHORT_DEADLINE_S):
            order = support.admit_order(kernel, case.plugin, case.args)
    else:
        order = support.admit_order(kernel, case.plugin, case.args)
    thread = support.drive_in_thread(kernel, order)
    run_dir = support.run_dir_of(kernel, order.run_id)
    if case.cancel:
        support.wait_ready(run_dir)
        kernel.control.cancel(order.run_id)
    thread.join(timeout=tolerances.JOIN_WAIT_S + clock.stop_bound + support.SHORT_DEADLINE_S)
    assert not thread.is_alive()
    view = kernel.control.project.status(order.run_id)
    assert isinstance(view, RunView)
    return Finished(run_dir, view)


def group_stop_row(run_dir: Path) -> dict[str, Any]:
    (row,) = support.rows_of(run_dir, "group_stop")
    return row


@pytest.mark.proves(
    "WR-OWN-6", "WR-OWN-6:cleanup-failure-beside-primary", "core", "core", "LOGIC+PROC", "BOTH"
)
@pytest.mark.proves(
    "WR-CANCEL-3",
    "WR-CANCEL-3:unconfirmed-never-clean-cancel-deadline",
    "core",
    "core",
    "LOGIC+PROC",
    "BOTH",
)
def test_injected_confirmation_failure_keeps_primary() -> None:
    """The cancel and the deadline; the pass (its stop goes through the stopper only under K-8)
    is `test_injected_confirmation_failure_keeps_primary_on_the_pass`, a CK-8 node."""
    for case in CASES:
        if case.label != "passed":
            _confirmation_failure_keeps_primary(case)


@pytest.mark.proves(
    "WR-OWN-6", "WR-OWN-6:cleanup-failure-beside-primary", "core", "core", "LOGIC+PROC", "BOTH"
)
@pytest.mark.proves("WR-OWN-3", "WR-OWN-3:success-no-survivor", "core", "core", "PROC", "BOTH")
def test_injected_confirmation_failure_keeps_primary_on_the_pass() -> None:
    """K-8 (REAP_ON_SUCCESS): a succeeded run's stop goes through the stopper, so a withheld
    confirmation projects `unknown` beside the unchanged `passed`."""
    (passed,) = [case for case in CASES if case.label == "passed"]
    _confirmation_failure_keeps_primary(passed)


def _confirmation_failure_keeps_primary(case: Case) -> None:
    confirmed = drive(None, case)
    withheld = drive(unconfirmed_after_real_stop, case)

    # the class is what a confirmed stop gives, for the pass, the cancel and the deadline
    assert confirmed.view.state == withheld.view.state == case.state, case.label
    assert confirmed.view.outcome is not None and withheld.view.outcome is not None
    assert withheld.view.outcome["class"] == confirmed.view.outcome["class"], case.label
    assert withheld.view.outcome == confirmed.view.outcome, case.label
    assert withheld.view.outcome["class"] == case.klass, case.label
    assert withheld.view.error == confirmed.view.error, case.label
    assert support.kinds(withheld.run_dir)[-1] == case.state

    # the disposition is relayed beside it: released when confirmed, unknown when not
    assert confirmed.view.cleanup is not None
    assert confirmed.view.cleanup.processes == "released", case.label
    assert withheld.view.cleanup is not None
    assert withheld.view.cleanup.processes == "unknown", case.label
    assert group_stop_row(withheld.run_dir)["confirmed_gone"] is False
    # the failed confirmation is one more key, not a change to the answer's own fields
    assert withheld.view.to_dict()["cleanup"] == {"processes": "unknown"}


def table_holds_a_process_of(run_dir: Path) -> bool:
    """MC-13's oracle: a fresh table read finds a live process carrying the run's tmp path."""
    return bool(support.marked(run_dir.name))


def assert_disposition_matches_table(finished: Finished, expected: str) -> None:
    processes = finished.view.cleanup.processes if finished.view.cleanup else None
    assert processes == expected, (processes, expected)
    assert processes != "nothing_created"  # a spawned run always has its group target
    # the disposition is `released` exactly when the table holds nothing of the run
    assert (processes == "released") == (not table_holds_a_process_of(finished.run_dir))


@pytest.mark.proves(
    "WR-OWN-6", "WR-OWN-6:disposition-matches-process-table", "core", "core", "LOGIC+PROC", "BOTH"
)
def test_reported_disposition_matches_ancestry_snapshot() -> None:
    # a group already gone at exit: nothing to signal, `released`, method exit
    gone = drive(None, Case("gone", "echo", {"message": "gone"}, "succeeded", "passed"))
    assert_disposition_matches_table(gone, "released")
    assert group_stop_row(gone.run_dir)["method"] == "exit"

    # a stopped group: the tree was alive when the stop began, none of it is after
    kernel = support.spine_kernel()
    order = support.admit_order(kernel, "tree", {"seconds": HOLD_S})
    thread = support.drive_in_thread(kernel, order)
    run_dir = support.run_dir_of(kernel, order.run_id)
    live = support.observe_tree(kernel, order.run_id).live
    assert live, "the tree was observed live before the stop"
    kernel.control.cancel(order.run_id)
    thread.join(timeout=tolerances.JOIN_WAIT_S + clock.stop_bound)
    assert not thread.is_alive()
    view = kernel.control.project.status(order.run_id)
    assert isinstance(view, RunView)
    stopped = Finished(run_dir, view)
    assert_disposition_matches_table(stopped, "released")
    assert group_stop_row(run_dir)["method"] == "signal"
    assert not ancestry.survivors(live, ancestry.snapshot())

    # an unconfirmed stop over a survivor: `unknown`, and the table still holds the process
    survivor_case = Case("survivor", "leaver", {"seconds": HOLD_S}, "succeeded", "passed")
    held = drive(unconfirmed_without_stopping, survivor_case)
    try:
        assert_disposition_matches_table(held, "unknown")
        assert table_holds_a_process_of(held.run_dir)
        assert held.view.outcome is not None and held.view.outcome["class"] == "passed"
    finally:  # the injected stopper killed nothing: the fixture removes what the plugin left
        ancestry.reap(support.marked(held.run_dir.name))
    assert not table_holds_a_process_of(held.run_dir)
