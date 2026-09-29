"""Lane-A shared scaffolding (P0-1A). Imports only `tests.proof.*` contracts
and product surfaces; never another lane (`tests.pins.<other>`).

Every timing bound is read through `tests.proof.tolerances` (SA-05); no
timing literal appears in a lane-A file.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from tests.proof import ancestry, harness, records, tolerances
from trestle.common.types import AdmitRequest, RequestOutcome, WorkOrder
from trestle.server.ledger import RunLedger, ledger_path, run_dir_for
from trestle.server.main import Kernel

LANE_PLUGIN_DIR = Path(__file__).resolve().parent / "plugins"
SHARED_PLUGIN_DIR = harness.DEFAULT_PLUGIN_DIR

# A run-scoped timeout, in whole seconds, for a run that must end by its
# deadline inside a test: several settle intervals, derived from the proof
# court's own patience constants.
SHORT_RUN_TIMEOUT_S = int(tolerances.SETTLE_LONG_S * 3)


def lane_kernel(home: Path | None = None) -> Kernel:
    """A kernel over the lane's plugins plus the shared fixture plugins."""
    return harness.fresh_kernel(plugin_dirs=[LANE_PLUGIN_DIR, SHARED_PLUGIN_DIR], home=home)


def wait_until(predicate: Callable[[], bool], bound_s: float) -> bool:
    """Poll `predicate` every `tolerances.POLL_S` up to `bound_s` seconds."""
    deadline = time.monotonic() + bound_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(tolerances.POLL_S)
    return predicate()


def admit_order(kernel: Kernel, plugin: str, args: dict[str, object] | None = None) -> WorkOrder:
    """Admit a run through the real admission port and build the WorkOrder
    the control surface would hand the conductor (the run is not driven)."""
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
        run_id=result.run_id,
        snapshot_id=snap.snapshot_id,
        spec_hash=str(created.get("spec_hash", "")),
    )


def drive_in_thread(kernel: Kernel, order: WorkOrder) -> threading.Thread:
    thread = threading.Thread(target=kernel.control.conductor.drive, args=(order,), daemon=True)
    thread.start()
    return thread


def run_dir_of(kernel: Kernel, run_id: str) -> Path:
    return run_dir_for(kernel.home, run_id)


def terminal_of(run_dir: Path) -> str | None:
    return records.node_record(run_dir).terminal


def marked(marker: str) -> set[ancestry.ProcInfo]:
    """Every process in a fresh snapshot whose argv carries `marker`."""
    return {p for p in ancestry.snapshot() if marker in p.argv}


def alive_marked(before: set[ancestry.ProcInfo], marker: str) -> set[ancestry.ProcInfo]:
    """The members of `before` still alive *and still marker-bearing* now
    (a zombie has no argv, so it is never counted as a survivor)."""
    return ancestry.survivors(before, marked(marker))


@contextmanager
def reaping(marker: str) -> Iterator[None]:
    """Every detached process carrying `marker` is killed on exit, under
    `ancestry.reap`, whatever the test did."""
    try:
        yield
    finally:
        ancestry.reap(marked(marker))


@dataclass
class Tree:
    """A run's attributable process tree observed while it was live."""

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
    def grandchild(self) -> set[ancestry.ProcInfo]:
        return {p for p in self.live if "time.sleep" in p.argv}


def observe_live_tree(kernel: Kernel, run_id: str) -> Tree:
    """Wait until the `detach` plugin reports its grandchild spawned, then
    snapshot the run's attributable tree (marker = the run's tmp path)."""
    run_dir = run_dir_of(kernel, run_id)
    ready = run_dir / "work" / "tmp" / "ready"
    assert wait_until(ready.exists, tolerances.JOIN_WAIT_S), "detach plugin never became ready"
    time.sleep(tolerances.SETTLE_SHORT_S)
    tree = Tree(marker=run_id, live=marked(run_id))
    assert tree.wrapper and tree.child and tree.grandchild, tree.live
    return tree
