"""CL-A1.2 run capacity (MC-30, SA-05; OQ-10): `max_running_runs` slots plus a bounded FIFO of
`queue_depth` waiting runs. A full queue refuses `admission.queue_full` before a run exists; a run
queued past its admitted deadline ends `timed_out` without ever being started.

Every timing bound comes from `tests.proof.tolerances` or `trestle.common.clock` (SA-05); no timing
literal appears here.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from tests.core.spine import support
from tests.proof import harness, mcp_host, records, tolerances
from trestle.common import clock, codes
from trestle.common.types import RunView, WorkOrder
from trestle.server import config as server_config
from trestle.server.scheduler import Scheduler

# Both capacity knobs at their smallest useful value: one slot, one waiting run.
SLOTS = 1
DEPTH = 1
# A plugin that holds its slot for a couple of settle units.
HOLD_S = tolerances.SETTLE_LONG_S * 2
# A run budget of a few settle units, in whole seconds.
BUDGET_S = int(tolerances.SETTLE_LONG_S * 3)
# A plugin that outlives the whole test; every run holding a slot is cancelled before it ends.
LONG_S = tolerances.JOIN_WAIT_S * 6
NO_WAIT_MS = 0


def _capacity_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRESTLE_MAX_RUNNING_RUNS", str(SLOTS))
    monkeypatch.setenv("TRESTLE_QUEUE_DEPTH", str(DEPTH))


def _run_dirs(home: Path) -> list[Path]:
    return sorted((home / "runs").glob("*/r_*"))


def _run_dir(home: Path, run_id: str) -> Path:
    (found,) = sorted((home / "runs").glob(f"*/{run_id}"))
    return found


@pytest.mark.proves(
    "WR-TERM-7", "WR-TERM-7:over-capacity-queued-or-refused", "core", "core", "MCP+PROC", "CI"
)
def test_over_capacity_queued_then_dispatched_or_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _capacity_env(monkeypatch)
    with mcp_host.McpHost(home=tmp_path / "host-home") as host:
        first = host.call("run", {"plugin": "slow", "args": {"seconds": HOLD_S}, "wait_ms": 0})
        second = host.call("run", {"plugin": "echo", "args": {"message": "x"}, "wait_ms": 0})
        assert first["run_id"] != second["run_id"]
        first_dir = _run_dir(host.home, first["run_id"])
        second_dir = _run_dir(host.home, second["run_id"])

        # the second run waits for the slot: admitted, not started, no process yet
        assert support.wait_until(
            lambda: "started" in records.node_record(first_dir).kinds,
            tolerances.JOIN_WAIT_S,
        )
        assert second["state"] == "queued", second
        assert records.node_record(second_dir).terminal is None
        assert "started" not in records.node_record(second_dir).kinds

        # the third is refused before it has a run directory
        third = host.call("run", {"plugin": "echo", "args": {"message": "y"}, "wait_ms": 0})
        assert third["code"] == codes.QUEUE_FULL, third
        assert "run_id" not in third
        assert [path.name for path in _run_dirs(host.home)] == sorted(
            [first["run_id"], second["run_id"]]
        )

        # when the slot frees the queued run is dispatched, and runs to its own terminal row
        assert support.wait_until(
            lambda: records.node_record(second_dir).terminal is not None,
            tolerances.HARNESS_WAIT_MS / 1000,
        )
        assert records.node_record(first_dir).terminal == "succeeded"
        assert records.node_record(second_dir).terminal == "succeeded"
        assert "started" in records.node_record(second_dir).kinds
        # a slot is free again: a new run is admitted, not refused
        again = host.call(
            "run",
            {"plugin": "echo", "args": {"message": "z"}, "wait_ms": tolerances.HARNESS_WAIT_MS},
        )
        assert again["state"] == "succeeded", again


@pytest.mark.proves(
    "WR-DEADLINE-2", "WR-DEADLINE-2:queued-no-extra-time", "core", "core", "MCP+PROC", "CI"
)
@pytest.mark.proves(
    "WR-OWN-8", "WR-OWN-8:dispatch-wait-within-deadline-or-refuse", "core", "core", "MCP+PROC", "CI"
)
def test_queued_run_gets_no_extra_time(monkeypatch: pytest.MonkeyPatch) -> None:
    _capacity_env(monkeypatch)
    kernel = support.spine_kernel()
    assert kernel.control.scheduler.max_running == SLOTS
    assert kernel.control.scheduler.queue_depth == DEPTH
    holder = kernel.control.run(plugin="slow", args={"seconds": LONG_S}, wait_ms=NO_WAIT_MS)
    assert isinstance(holder, RunView)
    holder_dir = support.run_dir_of(kernel, holder.run_id)
    try:
        assert support.wait_until(
            lambda: "started" in support.kinds(holder_dir), tolerances.JOIN_WAIT_S
        )
        # the queued run's whole budget is BUDGET_S from its admission, the wait included
        admitted_at = time.time()
        with harness.patch_snapshot(kernel, "echo", timeout_s=BUDGET_S):
            answer = kernel.control.run(
                plugin="echo",
                args={"message": "x"},
                wait_ms=BUDGET_S * 1000,
                completion="terminal",
            )
        answered_in = time.time() - admitted_at
        assert isinstance(answer, RunView), answer
        assert answer.state == "timed_out", answer.state
        assert BUDGET_S <= answered_in <= BUDGET_S + clock.finalization_margin

        run_dir = support.run_dir_of(kernel, answer.run_id)
        kinds = support.kinds(run_dir)
        # never started: no spawn, no identity, no group stop; the deadline error stands
        for absent in ("started", "process_identity", "group_stop"):
            assert absent not in kinds, kinds
        assert kinds[-3:] == ["error_record", "evidence_finalized", "timed_out"], kinds
        assert (
            answer.error is not None and answer.error["code"] == codes.EXECUTION_DEADLINE_EXCEEDED
        )
        # no process-group target exists (B2-C12)
        assert answer.cleanup is not None and answer.cleanup.processes == "nothing_created"
        # it left the queue: the slot and the waiting place are the holder's and free
        assert answer.run_id not in kernel.control.scheduler.queue
        assert not kernel.control.scheduler.waiting
    finally:
        kernel.control.cancel(holder.run_id)
        support.wait_until(
            lambda: records.node_record(holder_dir).terminal is not None,
            clock.stop_bound + clock.poll_interval + tolerances.SETTLE_LONG_S,
        )


def _order(n: int) -> WorkOrder:
    return WorkOrder(run_id=f"r_fifo_{n}", snapshot_id="s", spec_hash="h")


def test_dispatch_is_fifo_on_complete() -> None:
    scheduler = Scheduler(max_running=SLOTS, queue_depth=3)
    started: list[str] = []
    scheduler.on_dispatch = lambda order: started.append(order.run_id)
    far = time.monotonic() + LONG_S
    orders = [_order(n) for n in range(4)]
    for order in orders:
        scheduler.mint(order.run_id, order.snapshot_id, order.spec_hash)
        assert scheduler.enqueue(order, far).tag == "queued"
    assert started == ["r_fifo_0"]  # one slot: the rest wait, oldest first
    for n, order in enumerate(orders[:-1]):
        scheduler.complete(order.run_id)
        assert started == [f"r_fifo_{k}" for k in range(n + 2)]
    scheduler.complete(orders[-1].run_id)
    assert not scheduler.queue and not scheduler.running and not scheduler.waiting


def test_expiry_is_off_the_dispatch_path() -> None:
    """A run whose deadline passes while it waits is finalized by the timer, with the slot still
    held by another; a run already past its deadline at enqueue is finalized at once."""
    scheduler = Scheduler(max_running=SLOTS, queue_depth=DEPTH)
    started: list[str] = []
    expired: list[str] = []
    scheduler.on_dispatch = lambda order: started.append(order.run_id)
    scheduler.on_expire = lambda order: expired.append(order.run_id)
    holder, waiter, late = _order(0), _order(1), _order(2)
    for order in (holder, waiter, late):
        scheduler.mint(order.run_id, order.snapshot_id, order.spec_hash)
    scheduler.enqueue(holder, time.monotonic() + LONG_S)
    scheduler.enqueue(waiter, time.monotonic() + tolerances.POLL_FINE_S)
    scheduler.enqueue(late, time.monotonic() - tolerances.POLL_FINE_S)
    assert support.wait_until(lambda: len(expired) == 2, tolerances.JOIN_WAIT_S)
    assert set(expired) == {late.run_id, waiter.run_id}
    assert started == [holder.run_id]
    assert list(scheduler.queue) == [holder.run_id]


def test_capacity_defaults_are_provisional_and_overridable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert server_config.CAPACITY_DEFAULT_PROVISIONAL  # TM-C3's probe
    defaults = server_config.load_config(tmp_path)
    assert defaults.max_running_runs == server_config.MAX_RUNNING_RUNS_DEFAULT
    assert defaults.queue_depth == server_config.QUEUE_DEPTH_DEFAULT
    (tmp_path / "config.toml").write_text("max_running_runs = 3\nqueue_depth = 5\n")
    from_file = server_config.load_config(tmp_path)
    assert (from_file.max_running_runs, from_file.queue_depth) == (3, 5)
    _capacity_env(monkeypatch)
    assert server_config.load_config(tmp_path).max_running_runs == SLOTS
    assert server_config.load_config(tmp_path).queue_depth == DEPTH
