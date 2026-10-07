"""Scaffolding shared by the CS-2 spine tests: a kernel over the spine fixture plugins, run
admission without a drive, the run's process tree read through the proof court's ancestry seam,
and the identity-before-signal oracle.

Every timing bound comes from `tests.proof.tolerances` (SA-05); no timing literal appears here.
"""

from __future__ import annotations

import json
import os
import signal
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from tests.proof import ancestry, harness, records, tolerances
from trestle.common.fsutil import read_ndjson
from trestle.common.types import AdmitRequest, RequestOutcome, WorkOrder
from trestle.server.ledger import RunLedger, ledger_path, run_dir_for
from trestle.server.main import Kernel
from trestle.server.procident import SIDECAR_FILE, Attribution, Identity, ProcRow

START = 1_000  # a process start token in the planted tables below

# A marker names one run (its run id or its tmp path); anything shorter matches unrelated processes.
MIN_MARKER = 8

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
    """The ledger's rows of `kind`; for `process_identity`, the sidecar's too (v0.3.1 Problem C:
    the ledger keeps only the leader's row, every identity is in `evidence/processes.ndjson`),
    each identity once."""
    found = [row for row in rows(run_dir) if row.get("kind") == kind]
    if kind == "process_identity":
        seen = {(r.get("pid"), r.get("start")) for r in found}
        for row in read_ndjson(run_dir / "evidence" / SIDECAR_FILE):
            if (row.get("pid"), row.get("start")) not in seen:
                seen.add((row.get("pid"), row.get("start")))
                found.append(row)
    return found


def wait_ready(run_dir: Path) -> None:
    """Wait for the plugin's ready file. If the run ended before the plugin was ready because
    start-up alone passed the admitted deadline, this host is too slow to exercise the spawned
    path: skip with the reason, never assert the wrong path (RACES-REPORT L-7)."""
    ready = run_dir / "work" / "tmp" / "ready"
    awaited = records.await_record(run_dir, lambda _lane, _node: ready.exists())
    if awaited.why == "terminal" and not ready.exists():
        deadline = _admitted_deadline(run_dir)
        if deadline is not None and datetime.now(UTC) >= deadline:
            pytest.skip("start-up passed the admitted deadline before the plugin was ready")
    assert ready.exists(), "the plugin never became ready"
    time.sleep(tolerances.SETTLE_SHORT_S)  # absence-window


def _admitted_deadline(run_dir: Path) -> datetime | None:
    """`spec.deadline` (B2-C5) as an aware instant, read as the conductor reads it; None when the
    spec is unreadable or carries no deadline."""
    try:
        spec = json.loads((run_dir / "evidence" / "spec.json").read_text(encoding="utf-8"))
        fixed = datetime.fromisoformat(spec["deadline"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return fixed if fixed.tzinfo is not None else fixed.replace(tzinfo=UTC)


def marked(marker: str) -> set[ancestry.ProcInfo]:
    """Every process in a fresh snapshot whose argv carries `marker` (a run id or a path: an empty
    or short marker would match every process the user owns, so it is refused)."""
    assert len(marker) >= MIN_MARKER, f"marker {marker!r} is too short to name one run"
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
        rowed = {int(row["pid"]) for row in rows_of(self.run_dir, "process_identity")}
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


# -- a planted boot id, process table and signaller ------------------------------------------

START = 1_000


class FakeHost:
    """A planted boot id, process table and signaller in one: a signal ends a process unless it
    ignores that signal, exactly as the OS would, and every signal is recorded."""

    def __init__(self) -> None:
        self.rows: dict[int, ProcRow] = {}
        self.ignores: dict[int, set[int]] = {}
        self.sent: list[tuple[str, int, int, float]] = []
        self.rowed_at_signal: list[set[int]] = []
        self.recorded: list[Identity] = []
        self.boot = "boot-a"
        self.reads = 0  # every time the process table or the boot identity was read

    def add(
        self, pid: int, ppid: int, pgid: int, *, start: int = START, ignore: tuple[int, ...] = ()
    ) -> None:
        self.rows[pid] = ProcRow(pid=pid, ppid=ppid, pgid=pgid, start=start)
        self.ignores[pid] = set(ignore)

    # ProcessSource
    def boot_id(self) -> str:
        self.reads += 1
        return self.boot

    def table(self) -> dict[int, ProcRow]:
        self.reads += 1
        return dict(self.rows)

    def row(self, pid: int) -> ProcRow | None:
        self.reads += 1
        return self.rows.get(pid)

    # Signaller
    def signal_pid(self, pid: int, signum: int) -> None:
        self._note("pid", pid, signum)
        self._deliver(pid, signum)

    def signal_group(self, pgid: int, signum: int) -> None:
        self._note("group", pgid, signum)
        for pid in [p for p, r in self.rows.items() if r.pgid == pgid]:
            self._deliver(pid, signum)

    def _note(self, kind: str, target: int, signum: int) -> None:
        self.sent.append((kind, target, int(signum), time.monotonic()))
        self.rowed_at_signal.append({ident.pid for ident in self.recorded})

    def _deliver(self, pid: int, signum: int) -> None:
        if pid in self.rows and int(signum) not in self.ignores.get(pid, set()):
            del self.rows[pid]

    def attribution(self, group: int) -> Attribution:
        return Attribution(group=group, record=self.recorded.append, source=self)
