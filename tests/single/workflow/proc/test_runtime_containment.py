"""L.SL-3.4: the PROC falsifiers of A6.1:single and WR-CANCEL-4:A-PROC, through the runtime.

Core proved a cancel reaches a plain plugin's whole process tree (CS-2). A workflow plugin acts
through the runtime's own command runner and local-process port, so the same claims are asked again
of a run whose work the LOOP launched: the tree a bound command starts must go away after a cancel
although it ignores SIGTERM and left the run's session, and a resource the run created must be
released within the reserve while a resource it merely found is not touched.

Each test runs the `proc_leaf` fixture (tests/fixtures/workflows/proc_leaf.py) as a real run: a
kernel admits it, a wrapper and a child process execute it, and `run_tree` calls the real ports.
"""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tests.core.spine import support
from tests.proof import ancestry, records, tolerances
from tests.single.workflow.proc import procrun
from trestle.common import clock


@pytest.fixture(autouse=True)
def _short_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    procrun.short_stop(monkeypatch)


@pytest.mark.proves("A6.1", "A6.1:single", "A", "single", "PROC", "BOTH")
def test_runtime_launched_tree_ignoring_sigterm_gone_after_cancel(tmp_path: Path) -> None:
    """The bound command starts a process in its own session that ignores SIGTERM and holds a
    grandchild that also ignores it; the leaf never consults the cancel signal beyond the port's
    own. A cancel ends every process of the run by the stop bound, and it is SIGKILL after `grace`,
    not the SIGTERM, that ends the ones that resisted."""
    kernel = procrun.kernel_over_fixture(tmp_path)
    tag = procrun.tag_for(tmp_path, "containment")
    with procrun.started(
        kernel, tag, mode="command", seconds=tolerances.JOIN_WAIT_S * 6, hold_term=True
    ) as (order, run_dir, thread):
        procrun.wait_for(lambda: len(support.marked(tag)) >= 3, "command, detached, grandchild")
        tree = support.marked(tag)
        detached = {p for p in tree if p.sid == p.pid}
        assert len(detached) == 1, tree  # the one process that left the run's session
        (leader,) = detached
        grandchildren = {p for p in tree if p.ppid == leader.pid}
        assert len(grandchildren) == 1, tree  # its own child, in its session
        (command,) = tree - detached - grandchildren
        assert command.sid != leader.sid and command.pgid != leader.pgid
        assert grandchildren <= {p for p in tree if p.sid == leader.sid}
        resisting = {leader, *grandchildren}
        # every resisting process has an identity row before anything signals it (V-2.3)
        procrun.wait_for(
            lambda: {p.pid for p in resisting} <= procrun.identity_pids(run_dir),
            "identity rows for the detached tree",
        )
        died: list[float] = []

        def watch() -> None:
            while support.alive_marked(resisting, tag):
                time.sleep(tolerances.POLL_FINE_S)
            died.append(time.time())

        sampler = threading.Thread(target=watch, daemon=True)
        sampler.start()

        requested = time.time()
        kernel.control.cancel(order.run_id)
        thread.join(timeout=tolerances.JOIN_WAIT_S + clock.stop_bound)
        returned = time.time()
        assert not thread.is_alive(), "conductor never returned"
        sampler.join(timeout=tolerances.JOIN_WAIT_S)

        assert support.kinds(run_dir)[-1] == "cancelled"
        assert returned - requested <= clock.stop_bound + tolerances.PROC_WAIT_S
        # the MC-13 ancestry of the run is empty by the stop bound
        assert support.wait_until(
            lambda: not support.alive_marked(tree, tag), clock.stop_bound + tolerances.PROC_WAIT_S
        )
        (stop,) = support.rows_of(run_dir, "group_stop")
        assert stop["confirmed_gone"] is True
        assert died, "the detached tree never went away"
        assert died[0] - requested >= clock.grace - tolerances.POLL_S, "SIGTERM cannot end them"
        assert died[0] - requested <= clock.stop_bound + tolerances.POLL_S
        # the tree was the runtime's: the loop issued the command through the ExecutionPort
        (ticket,) = records.lane_tickets(records.lane_rows(run_dir))
        assert ticket["issue"].entry["effect"] == "run"
        assert ticket["result"].entry["code"] == "execution.cancelled"


@pytest.mark.proves("WR-CANCEL-4", "WR-CANCEL-4:A-PROC", "A", "single", "PROC", "BOTH")
def test_created_resource_released_on_cancel_within_reserve(tmp_path: Path) -> None:
    """A cancel while the run waits for its created process to become ready releases that process
    through the release walk, inside the release slice and before any kill; a process that was
    already running the same command line (found) keeps its pid, start token and command line and
    is never signalled."""
    kernel = procrun.kernel_over_fixture(tmp_path)
    tag = procrun.tag_for(tmp_path, "released")
    with support.reaping(tag):
        found = procrun.start_found(tag)
        try:
            procrun.wait_for(lambda: procrun.find(tag, found.pid) is not None, "the found process")
            before = procrun.find(tag, found.pid)
            assert before is not None
            with procrun.started(kernel, tag, mode="resource") as (order, run_dir, thread):
                procrun.wait_for(lambda: len(support.marked(tag)) >= 2, "the created process")
                (created,) = {p for p in support.marked(tag) if p.pid != found.pid}
                confirmed = lambda: [  # noqa: E731
                    r for r in records.lane_rows(run_dir).rows if r.cls == "confirmation"
                ]
                procrun.wait_for(confirmed, "the create confirmed")
                requested = datetime.now(UTC)
                kernel.control.cancel(order.run_id)
                thread.join(timeout=tolerances.JOIN_WAIT_S + clock.stop_bound)
                assert not thread.is_alive(), "conductor never returned"

                assert support.kinds(run_dir)[-1] == "cancelled"
                (ticket,) = [
                    t
                    for t in records.lane_tickets(records.lane_rows(run_dir))
                    if t["issue"].entry["effect"] == "up"
                ]
                released = ticket["released"]
                assert released is not None, "the created process has no released entry"
                assert released.entry["outcome"] is None
                took = procrun.lane_time(released.entry["released_at"]) - requested
                assert timedelta(0) <= took <= timedelta(seconds=clock.release_slice)
                # the release walk did it, not the kill: the port's own stop signalled it and the
                # group needed no signal at the end
                assert procrun.signals_received(tag, created.pid) == [15]
                assert support.alive_marked({created}, tag) == set()
                (stop,) = support.rows_of(run_dir, "group_stop")
                assert (stop["confirmed_gone"], stop["method"]) == (True, "exit")

                after = procrun.find(tag, found.pid)
                assert after is not None, "the found process is gone"
                assert (after.pid, after.start, after.argv) == (
                    before.pid,
                    before.start,
                    before.argv,
                )
                assert procrun.signals_received(tag, found.pid) == []
                assert found.poll() is None
        finally:
            found.kill()
            found.wait()
            ancestry.reap(support.marked(tag))
