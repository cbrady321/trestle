"""Scaffolding shared by the CS-2 spine tests: a kernel over the spine fixture plugins, run
admission without a drive, the run's process tree read through the proof court's ancestry seam,
and the identity-before-signal oracle.

Every timing bound comes from `tests.proof.tolerances` (SA-05); no timing literal appears here.
"""

from __future__ import annotations

import os
import signal
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tests.proof import ancestry, harness, records, tolerances
from trestle.common.types import AdmitRequest, RequestOutcome, WorkOrder
from trestle.server.ledger import RunLedger, ledger_path, run_dir_for
from trestle.server.main import Kernel

SPINE_PLUGIN_DIR = Path(__file__).resolve().parent / "plugins"

# A run-scoped deadline, in whole seconds, for a run that must end by its deadline inside a test:
# a few settle intervals, derived from the proof court's own patience constants.
SHORT_DEADLINE_S = int(tolerances.SETTLE_LONG_S * 3)
# A stop's own bound in a test that shrinks `grace` and `kill` to keep the suite fast.
TEST_GRACE_S = tolerances.SETTLE_LONG_S
TEST_KILL_S = tolerances.SETTLE_LONG_S * 2


def spine_kernel(home: Path | None = None) -> Kernel:
    """A kernel over the spine fixture plugins plus the shared fixture plugins."""
    return harness.fresh_kernel(
        plugin_dirs=[SPINE_PLUGIN_DIR, harness.DEFAULT_PLUGIN_DIR], home=home
    )


def wait_until(predicate: Callable[[], bool], bound_s: float) -> bool:
    deadline = time.monotonic() + bound_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(tolerances.POLL_S)
    return predicate()


def admit_order(kernel: Kernel, plugin: str, args: dict[str, object] | None = None) -> WorkOrder:
    """Admit a run through the real admission port and build the WorkOrder the control surface
    would hand the conductor; the run is not driven."""
    result = kernel.control.admission.admit(AdmitRequest(plugin=plugin, args=args or {}))
    if result.tag == "refused":
        assert isinstance(result.outcome, RequestOutcome)
        raise RuntimeError(f"admission refused: {result.outcome.code}")
    run_dir = run_dir_for(kernel.home, result.run_id)
    created = RunLedger.open(ledger_path(run_dir)).last_kind("created")
    assert created is not None
    snap = kernel.registry.get(plugin)
    assert snap is not None
    return WorkOrder(
        run_id=result.run_id, snapshot_id=snap.snapshot_id, spec_hash=str(created["spec_hash"])
    )


def drive_in_thread(kernel: Kernel, order: WorkOrder) -> threading.Thread:
    thread = threading.Thread(target=kernel.control.conductor.drive, args=(order,), daemon=True)
    thread.start()
    return thread


def run_dir_of(kernel: Kernel, run_id: str) -> Path:
    return run_dir_for(kernel.home, run_id)


def rows(run_dir: Path) -> list[dict[str, Any]]:
    return records.ledger_rows(run_dir).rows


def kinds(run_dir: Path) -> list[str]:
    return records.node_record(run_dir).kinds


def rows_of(run_dir: Path, kind: str) -> list[dict[str, Any]]:
    return [row for row in rows(run_dir) if row.get("kind") == kind]


def wait_ready(run_dir: Path) -> None:
    ready = run_dir / "work" / "tmp" / "ready"
    assert wait_until(ready.exists, tolerances.JOIN_WAIT_S), "the plugin never became ready"
    time.sleep(tolerances.SETTLE_SHORT_S)


def marked(marker: str) -> set[ancestry.ProcInfo]:
    """Every process in a fresh snapshot whose argv carries `marker`."""
    return {p for p in ancestry.snapshot() if marker in p.argv}


def alive_marked(before: set[ancestry.ProcInfo], marker: str) -> set[ancestry.ProcInfo]:
    """The members of `before` still alive and still marker-bearing now."""
    return ancestry.survivors(before, marked(marker))


@contextmanager
def reaping(marker: str) -> Iterator[None]:
    """Every process carrying `marker` is killed on exit, whatever the test did."""
    try:
        yield
    finally:
        ancestry.reap(marked(marker))


@dataclass
class Tree:
    """A run's process tree observed while it was live."""

    marker: str
    live: set[ancestry.ProcInfo] = field(default_factory=set)

    def role(self, token: str) -> set[ancestry.ProcInfo]:
        return {p for p in self.live if token in p.argv}

    @property
    def wrapper(self) -> set[ancestry.ProcInfo]:
        return self.role("trestle.wrapper.main")

    @property
    def child(self) -> set[ancestry.ProcInfo]:
        return self.role("trestle.child.main")

    @property
    def descendants(self) -> set[ancestry.ProcInfo]:
        """The plugin's grandchildren: a same-group one and a setsid'd one."""
        return {p for p in self.live if "time.sleep" in p.argv or "signal.SIGTERM" in p.argv}


def observe_tree(kernel: Kernel, run_id: str) -> Tree:
    """Wait until the `tree` plugin reports its processes spawned, then read the run's tree."""
    wait_ready(run_dir_of(kernel, run_id))
    tree = Tree(marker=run_id, live=marked(run_id))
    assert tree.wrapper and tree.child and len(tree.descendants) == 2, tree.live
    return tree


# -- the identity-before-signal oracle ------------------------------------------------------


@dataclass(frozen=True)
class Signal:
    """One signal the product sent: to a pid, or (`group`) to every member of a process group,
    with the pids of the run's own processes it reached and the pids that had an identity row
    in the run's ledger at the moment it was sent."""

    signum: int
    targets: frozenset[int]
    rowed: frozenset[int]
    at: float


def unrowed(sent: list[Signal]) -> list[tuple[int, int]]:
    """Every (signal, pid) that reached a run process which had no `process_identity` row yet."""
    return [(s.signum, pid) for s in sent for pid in sorted(s.targets - s.rowed)]


class SignalRecorder:
    """Records, from inside the product's process, every `os.kill` and `os.killpg` aimed at a
    process of `marker`'s run, and which of them had an identity row when it was sent. The real
    call is still made."""

    def __init__(self, run_dir: Path, marker: str) -> None:
        self.run_dir = run_dir
        self.marker = marker
        self.sent: list[Signal] = []
        self._real_kill = os.kill
        self._real_killpg = os.killpg
        self._lock = threading.Lock()

    def _note(self, signum: int, *, pid: int | None, group: int | None) -> None:
        snap = ancestry.snapshot()
        mine = {p.pid for p in snap if self.marker in p.argv}
        reached = {
            p.pid
            for p in snap
            if (pid is not None and p.pid == pid) or (group is not None and p.pgid == group)
        }
        rowed = {
            int(row["pid"]) for row in rows(self.run_dir) if row.get("kind") == "process_identity"
        }
        with self._lock:
            self.sent.append(
                Signal(signum, frozenset(reached & mine), frozenset(rowed), time.time())
            )

    def kill(self, pid: int, signum: int = signal.SIGTERM) -> None:
        self._note(int(signum), pid=pid, group=None)
        self._real_kill(pid, signum)

    def killpg(self, pgid: int, signum: int) -> None:
        self._note(int(signum), pid=None, group=pgid)
        self._real_killpg(pgid, signum)
