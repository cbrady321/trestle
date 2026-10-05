"""L.TR-L.6: WR-UNIT-4 lease inheritance, proved through the host.

Two real host calls, as a caller makes them: `lease_root_child` (a root that declares the
environment `E` over one child, `worker`, that projects `E` too) and `lease_direct_child` (the
worker called directly as a one-vertex root declaring `E`). The worker's mutation is held open
between two files in its run's tmp dir, `entered` and `exited`, until the test writes `go`, so the
test decides when a run's mutation is open and reads back exactly when it was: a run whose mutation
is open holds the environment, and a second run of the environment may not open its own until the
first ends (SL-8: one lease per key, held from the `created` row to the terminal row, FIFO). The
child of a root is never a lease holder, never queues and has no entry of its own: it runs under
the root's lease (L.TR-1.4 decides the tree's one key at admission).

The root is admitted first and the direct call second, the direct call carrying the later deadline:
admission refuses as busy a request whose deadline leaves less than its worst case after the
holder's (`admission.environment_busy`, SL-8's rule), so a tree behind a same-deadline holder is
refused and this order is the one that is admitted."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from tests.proof import records, tolerances
from tests.tree import hostpath
from tests.tree.test_tr1_admission import run_dirs, source
from trestle.common.types import PublishView, RunView
from trestle.server.main import Kernel

proves_clause = pytest.mark.proves("WR-UNIT-4", "A8.5", "A", "tree", "PROC+LOGIC", "CI")
proves_no_overlap = pytest.mark.proves(
    "WR-UNIT-4", "WR-UNIT-4:no-overlap-root-vs-direct", "A", "tree", "PROC+LOGIC", "CI"
)
proves_no_entry = pytest.mark.proves(
    "WR-UNIT-4", "WR-UNIT-4:no-child-lease-entry", "A", "tree", "PROC+LOGIC", "CI"
)
proves_not_queued = pytest.mark.proves(
    "WR-UNIT-4", "WR-UNIT-4:child-never-queued-behind-root", "A", "tree", "PROC+LOGIC", "CI"
)
proves_direct = pytest.mark.proves(
    "WR-UNIT-4", "WR-UNIT-4:direct-call-takes-lease", "A", "tree", "PROC+LOGIC", "CI"
)

ENV = "prod"
KEY = json.dumps(ENV)  # the canonical JSON of the environment argument: the lease key
ROOT, DIRECT = "lease_root_child", "lease_direct_child"
TERMINAL_KINDS = ("succeeded", "failed", "cancelled", "timed_out", "interrupted")


def wait_until(predicate: Callable[[], bool], bound_s: float = tolerances.JOIN_WAIT_S) -> bool:
    end = time.monotonic() + bound_s
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(tolerances.POLL_S)
    return predicate()


@dataclass
class Run:
    """One host call on its own thread, and the run directory it made."""

    kernel: Kernel
    plugin: str
    run_dir: Path
    thread: threading.Thread
    result: list[Any]

    @property
    def gate(self) -> Path:
        return self.run_dir / "work" / "tmp"

    @property
    def run_id(self) -> str:
        return self.run_dir.name

    def marked(self, name: str) -> bool:
        return (self.gate / name).exists()

    def mtime(self, name: str) -> int:
        return (self.gate / name).stat().st_mtime_ns

    def let_go(self) -> None:
        (self.gate / "go").write_text("1", encoding="utf-8")

    def finish(self) -> RunView:
        """The run's mutation may close (`go`) and the host call returns its terminal view."""
        self.let_go()
        self.thread.join(tolerances.JOIN_WAIT_S * 6)
        assert not self.thread.is_alive(), f"{self.plugin}: the host call never returned"
        (view,) = self.result
        assert isinstance(view, RunView), view
        assert view.state == "succeeded", view
        return view

    def kinds(self) -> list[str]:
        return [str(row["kind"]) for row in records.ledger_rows(self.run_dir).rows]


def publish_both(kernel: Kernel) -> None:
    for fixture in (ROOT, DIRECT):
        published = hostpath.publish_tree_via_host(kernel, source(fixture))
        assert isinstance(published, PublishView), published


def minted(kernel: Kernel, run_id: str) -> bool:
    """Whether the scheduler has minted `run_id` (read without its lock: a concurrent mutation of
    the queue is retried by the caller's poll)."""
    try:
        return run_id in kernel.control.scheduler.queue
    except RuntimeError:  # deque mutated during iteration
        return False


def call(kernel: Kernel, plugin: str) -> Run:
    """`ControlSurface.run(completion="terminal")` for `plugin` (environment `ENV`) on a thread;
    returns once admission has finished: the run directory exists *and* the scheduler has minted
    the run id. Admission writes the run directory (with its `created` row) before it records the
    holder and mints, on the caller's thread, so the directory alone is seen mid-admission."""
    before = set(run_dirs(kernel))
    result: list[Any] = []
    thread = threading.Thread(
        target=lambda: result.append(hostpath.run_tree_via_host(kernel, plugin, {"env": ENV}))
    )
    thread.start()
    wait_until(lambda: bool(set(run_dirs(kernel)) - before or result))
    assert not result or isinstance(result[0], RunView), f"{plugin} was refused: {result}"
    assert set(run_dirs(kernel)) - before, f"{plugin} was never admitted"
    (run_dir,) = set(run_dirs(kernel)) - before
    # minting is admission's last step (after the holder index is told); a run that has already
    # ended has left the queue, and its host call has returned
    assert wait_until(lambda: minted(kernel, run_dir.name) or bool(result)), (
        f"{plugin}: admission never finished"
    )
    return Run(kernel, plugin, run_dir, thread, result)


def waiting(kernel: Kernel) -> list[str]:
    """The run ids waiting in the scheduler's FIFO (read without its lock, as `minted`)."""
    try:
        return [w.order.run_id for w in kernel.control.scheduler.waiting]
    except RuntimeError:  # deque mutated during iteration
        return []


def mutation_open(run: Run) -> None:
    assert wait_until(lambda: run.marked("entered")), f"{run.plugin} never opened its mutation"
    assert not run.marked("exited")


def root_then_direct(kernel: Kernel) -> tuple[Run, Run]:
    """The root's worker is mid-mutation and a direct call of the worker (same environment) has been
    admitted behind it: both are returned with the root's mutation still open."""
    publish_both(kernel)
    root = call(kernel, ROOT)
    mutation_open(root)
    direct = call(kernel, DIRECT)
    return root, direct


@proves_clause
@proves_no_overlap
def test_root_and_direct_child_never_overlap(tree_kernel: Kernel) -> None:
    """Root(E) with its child, and a direct call to the child declaring E, admitted together: the
    direct call opens no mutation while the root's is open (however long the test looks), it opens
    its own only after the root's terminal row, and the two mutation intervals, read back from the
    files each run wrote, are disjoint."""
    root, direct = root_then_direct(tree_kernel)
    # the root's mutation is open: the direct call is queued behind it, not running
    time.sleep(tolerances.SETTLE_S * 3)  # absence-window
    assert not direct.marked("entered") and "started" not in direct.kinds()
    root_view = root.finish()
    assert wait_until(lambda: direct.marked("entered")), "the direct call never ran"
    assert root.kinds()[-1] in TERMINAL_KINDS  # the root had ended when the direct call opened
    direct_view = direct.finish()
    assert root_view.run_id != direct_view.run_id
    # the intervals, [entered, exited] of each run, are disjoint and ordered
    assert (
        root.mtime("entered")
        <= root.mtime("exited")
        <= direct.mtime("entered")
        <= direct.mtime("exited")
    )


@proves_clause
@proves_no_entry
@proves_not_queued
def test_child_never_queued_behind_root(tree_kernel: Kernel) -> None:
    """One root(E) run with its child: the child reaches its mutation while the root holds E (a
    child queued behind its own root would never start), the scheduler and the lease index show one
    run and one key with nothing waiting, and the root's record has one lease key (the `created`
    row's) and no lease, queue or acquire entry on any child path."""
    publish_both(tree_kernel)
    root = call(tree_kernel, ROOT)
    mutation_open(root)  # the child is running under the root's lease
    scheduler = tree_kernel.control.scheduler
    assert list(scheduler.queue) == [root.run_id]
    assert not scheduler.waiting
    assert scheduler.running_keys == {root.run_id: KEY}
    (holder,) = tree_kernel.control.admission.holders.held()
    assert (holder.run_id, holder.key) == (root.run_id, KEY)
    view = root.finish()
    assert view.to_dict()["answer"]["outcome"] == "passed"

    lane = records.lane_rows(root.run_dir)
    assert not lane.problems and not lane.torn, lane.problems
    ends = {row.path: row.entry for row in lane.rows if row.cls == "end"}
    assert ends["worker"]["condition"] == "satisfied"
    ledger = records.ledger_rows(root.run_dir).rows
    assert [row["lease_key"] for row in ledger if "lease_key" in row] == [KEY]  # one, the root's
    for row in lane.rows:
        words = {word for key in row.entry for word in key.split("_")}
        assert not words & {"lease", "acquire", "queue"}, row.entry
    assert not [row for row in ledger if {"lease", "queue"} & set(str(row["kind"]).split("_"))]
    assert scheduler.running_keys == {} and not scheduler.waiting


@proves_clause
@proves_direct
def test_direct_call_acquires_before_effect(tree_kernel: Kernel) -> None:
    """A direct call of the worker declaring E holds the lease before it makes any mutation:
    admitted behind a root that holds E, it is already a holder (its `created` row carries the
    lease key) and has written no lane entry and opened no mutation, and it opens its own mutation
    only after the root's terminal row."""
    root, direct = root_then_direct(tree_kernel)
    holders = tree_kernel.control.admission.holders
    assert [(h.run_id, h.key) for h in holders.held()] == [
        (root.run_id, KEY),
        (direct.run_id, KEY),
    ]  # in admission order: the direct call acquired at admission, behind the root
    (created,) = (
        row for row in records.ledger_rows(direct.run_dir).rows if row["kind"] == "created"
    )
    assert created["lease_key"] == KEY
    time.sleep(tolerances.SETTLE_S * 3)  # absence-window
    assert not direct.marked("entered")  # no effect while the root holds the environment
    assert not records.lane_rows(direct.run_dir).rows  # ... and not one lane entry
    # the direct call's thread enqueues it after admission returns: awaited, not read at once
    assert wait_until(lambda: waiting(tree_kernel) == [direct.run_id]), waiting(tree_kernel)
    root.finish()
    assert wait_until(lambda: direct.marked("entered"))
    direct.finish()
    # its first effect came after the acquisition: the lane's first entry is later than `created`
    assert direct.kinds()[0] == "created"
    first_effect = min(
        row.entry["at"] for row in records.lane_rows(direct.run_dir).rows if "at" in row.entry
    )
    assert created["at"] <= first_effect
