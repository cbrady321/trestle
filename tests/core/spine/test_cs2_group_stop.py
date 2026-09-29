"""CS-2 GroupStop row and cleanup projection (L.CS-2.6; MC-32, B2-C9): exactly one `group_stop`
on every terminal path of a spawned run, after the kill and before `evidence_finalized`; the
run's process-group cleanup is `released` or `unknown`, never `nothing_created`."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from tests.core.spine import support
from tests.proof import harness, tolerances
from trestle.common import clock
from trestle.common.types import RunView
from trestle.server import procident
from trestle.server.ledger import RunLedger, ledger_path
from trestle.server.procident import GroupStop
from trestle.server.recovery import seed_interrupted_run

UNCONFIRMED = GroupStop(confirmed_gone=False, signalled=True)


@pytest.fixture(autouse=True)
def short_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(clock, "grace", support.TEST_GRACE_S)
    monkeypatch.setattr(clock, "kill", support.TEST_KILL_S)


class SpyStopper:
    """Wraps the real stopper: notes the ledger kinds standing when the stop was called and
    when it returned, so the row's place after the kill is observable."""

    def __init__(self, run_dir_of: Any, answer: GroupStop | None = None) -> None:
        self.run_dir_of = run_dir_of
        self.answer = answer
        self.before: list[list[str]] = []

    def __call__(self, attribution: procident.Attribution) -> GroupStop:
        run_dir = next(iter(sorted(self.run_dir_of().glob("*/r_*"))))
        self.before.append(support.kinds(run_dir))
        return self.answer if self.answer is not None else procident.stop_group(attribution)


def _run(
    kernel: Any, plugin: str, args: dict[str, object], *, deadline: bool = False
) -> tuple[Path, RunView]:
    """Run to a terminal state through the control surface and return the run dir and view."""
    if deadline:
        with harness.patch_snapshot(kernel, plugin, timeout_s=support.SHORT_DEADLINE_S):
            order = support.admit_order(kernel, plugin, args)
    else:
        order = support.admit_order(kernel, plugin, args)
    thread = support.drive_in_thread(kernel, order)
    run_dir = support.run_dir_of(kernel, order.run_id)
    if not deadline and plugin in {"tree", "slow"}:
        support.wait_ready(run_dir) if plugin == "tree" else time.sleep(tolerances.SETTLE_LONG_S)
        kernel.control.cancel(order.run_id)
    thread.join(timeout=tolerances.JOIN_WAIT_S + clock.stop_bound + support.SHORT_DEADLINE_S)
    assert not thread.is_alive()
    view = kernel.control.project.status(order.run_id)
    assert isinstance(view, RunView)
    return run_dir, view


def _group_stops(run_dir: Path) -> list[dict[str, Any]]:
    return support.rows_of(run_dir, "group_stop")


def _assert_row_position(run_dir: Path) -> None:
    kinds = support.kinds(run_dir)
    assert kinds.count("group_stop") == 1
    assert kinds.index("started") < kinds.index("group_stop") < kinds.index("evidence_finalized")
    assert kinds[-1] in {"succeeded", "failed", "cancelled", "timed_out", "worker_exit"}


@pytest.mark.proves(
    "WR-OWN-6", "WR-OWN-6:gone-group-reports-released", "core", "core", "PROC", "BOTH"
)
def test_group_stop_row_on_every_terminal_path() -> None:
    outcomes: dict[str, tuple[Path, RunView, str]] = {}
    for label, plugin, args, deadline in (
        ("pass", "echo", {"message": "gs"}, False),
        ("fail", "leaver", {"seconds": tolerances.JOIN_WAIT_S * 6, "fail": True}, False),
        ("cancel", "tree", {"seconds": tolerances.JOIN_WAIT_S * 6}, False),
        ("deadline", "slow", {"seconds": tolerances.JOIN_WAIT_S * 6}, True),
    ):
        kernel = support.spine_kernel()
        spy = SpyStopper(lambda k=kernel: k.home / "runs")
        kernel.control.conductor.stopper = spy
        run_dir, view = _run(kernel, plugin, args, deadline=deadline)
        _assert_row_position(run_dir)
        (row,) = _group_stops(run_dir)
        # the row came after the stop was called: no group_stop stood when the stopper ran
        assert spy.before and all("group_stop" not in kinds for kinds in spy.before)
        outcomes[label] = (run_dir, view, row["method"])
        assert row["confirmed_gone"] is True
        assert view.cleanup is not None and view.cleanup.processes == "released"
        assert "cleanup" in view.to_dict() and view.to_dict()["cleanup"] == {
            "processes": "released"
        }
        assert support.marked(run_dir.name) == set(), label  # the projection agrees with the table
    # a plugin that starts nothing was not signalled; every path that left a process was
    assert outcomes["pass"][2] == "exit"
    assert outcomes["fail"][2] == outcomes["cancel"][2] == outcomes["deadline"][2] == "signal"
    assert outcomes["pass"][1].state == "succeeded"
    assert outcomes["fail"][1].state == "failed"
    assert outcomes["cancel"][1].state == "cancelled"
    assert outcomes["deadline"][1].state == "timed_out"


def test_unconfirmed_stopper_projects_unknown_on_every_path_pass_included() -> None:
    for plugin, args, deadline in (
        ("echo", {"message": "gs"}, False),
        ("leaver", {"seconds": support.SHORT_DEADLINE_S, "fail": True}, False),
        ("slow", {"seconds": tolerances.JOIN_WAIT_S * 6}, True),
    ):
        kernel = support.spine_kernel()
        kernel.control.conductor.stopper = SpyStopper(lambda k=kernel: k.home / "runs", UNCONFIRMED)
        run_dir, view = _run(kernel, plugin, args, deadline=deadline)
        try:
            (row,) = _group_stops(run_dir)
            assert row["confirmed_gone"] is False
            assert view.cleanup is not None and view.cleanup.processes == "unknown"
        finally:
            # the injected stopper killed nothing: clean up what the plugin left
            from tests.proof import ancestry

            ancestry.reap(support.marked(run_dir.name))


def test_a_plugin_that_starts_nothing_ends_released_never_nothing_created() -> None:
    kernel = support.spine_kernel()
    run_dir, view = _run(kernel, "echo", {"message": "nothing"})
    (row,) = _group_stops(run_dir)
    assert (row["confirmed_gone"], row["method"]) == (True, "exit")
    assert view.cleanup is not None
    assert view.cleanup.processes == "released"
    assert view.cleanup.processes != "nothing_created"


def _seeded(kernel: Any, *rows: tuple[str, dict[str, Any]]) -> RunView:
    run_dir = seed_interrupted_run(kernel.home, "r_gs_seed", last_kind="started")
    ledger = RunLedger.open(ledger_path(run_dir))
    for kind, fields in rows:
        ledger.append(kind, run_id="r_gs_seed", **fields)
    ledger.append(
        "evidence_finalized", run_id="r_gs_seed", completeness="complete", result_state="absent"
    )
    ledger.append("succeeded", run_id="r_gs_seed")
    view = kernel.control.project.status("r_gs_seed")
    assert isinstance(view, RunView)
    return view


def test_projection_reads_the_row_and_never_the_absence_as_clean() -> None:
    kernel = support.spine_kernel()
    assert _seeded(kernel).cleanup.processes == "unknown"  # type: ignore[union-attr]  # no row
    kernel = support.spine_kernel()
    view = _seeded(kernel, ("group_stop", {"confirmed_gone": False, "method": "signal"}))
    assert view.cleanup is not None and view.cleanup.processes == "unknown"
    kernel = support.spine_kernel()
    view = _seeded(kernel, ("group_stop", {"confirmed_gone": True, "method": "exit"}))
    assert view.cleanup is not None and view.cleanup.processes == "released"
    for processes in ("unknown", "released"):
        assert processes != "nothing_created"


def test_a_run_that_never_started_has_no_process_group_target() -> None:
    kernel = support.spine_kernel()
    run_dir = seed_interrupted_run(kernel.home, "r_gs_queued", last_kind="admitted")
    ledger = RunLedger.open(ledger_path(run_dir))
    ledger.append(
        "evidence_finalized", run_id="r_gs_queued", completeness="partial", result_state="absent"
    )
    ledger.append("cancelled", run_id="r_gs_queued")
    view = kernel.control.project.status("r_gs_queued")
    assert isinstance(view, RunView)
    assert view.cleanup is None and "cleanup" not in view.to_dict()
    assert not _group_stops(run_dir)
