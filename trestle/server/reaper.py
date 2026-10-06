"""The reaper (v0.4 Problem A, rule 3): recovery becomes reaping.

Each server runs one at its start and every 10 s, over `home/live/` only, never the whole runs/
tree. For each live marker it tries the run's owner lock without blocking: held, the owner is
alive and the run is left alone; taken, the owner is dead, so the reaper re-reads the ledger and,
unless it is already terminal, stops the run's processes, promotes its outputs when it had no
secret values, and finalizes it `interrupted` (all outside the admission lock, since a stop can
take grace + kill). Then, under the admission lock, it removes the marker and the run's
`home/sched.json` entry (its slot and environment key are free only now) and releases the owner
lock. The reaper is the only code that takes over
a dead owner's lock; a waiter that finds its run's lock free only wakes it (`wake`).

A v0.3.0 run (its `created` row names no owner) has no lock holder: it is finalized only once its
recorded leader is gone (B2-C11 branch ii, or i), never by stopping its processes (branch iii).

Debris: an `.adm-` directory or a marker without a run, left by an admission that died, is removed
only here and only under the admission lock (rule 1). A periodic pass finds debris through the
markers; the start pass (and `trestle recover`) also lists the month directories for `.adm-`
names an admission left before writing its marker.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from trestle.server import home as homes
from trestle.server import idempotency, procident
from trestle.server import pool as pools
from trestle.server.ledger import RunLedger, ledger_path
from trestle.server.procident import ProcessSource, Signaller
from trestle.server.recovery import recover_run_dir
from trestle.server.runstate import refresh_state

REAP_INTERVAL_S = 10.0


@dataclass
class ReapReport:
    examined: int = 0
    reaped: list[str] = field(default_factory=list)
    left_live: int = 0
    markers_removed: int = 0
    debris_removed: int = 0
    busy: bool = False
    keys_purged: int = 0


@dataclass
class Reaper:
    home: Path
    server_id: str | None = None
    source: ProcessSource | None = None
    signaller: Signaller | None = None
    interval_s: float = REAP_INTERVAL_S
    # each pass writes this server's `seen_at` (rule 5's reserve) through this hook
    # (`Scheduler.seen`)
    on_pass: Callable[[], None] | None = None
    _wake: threading.Event = field(default_factory=threading.Event, repr=False, compare=False)
    _thread: threading.Thread | None = field(default=None, repr=False, compare=False)
    _stop: threading.Event = field(default_factory=threading.Event, repr=False, compare=False)
    _pass_lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    # -- the background loop --------------------------------------------------------------------

    def start(self) -> None:
        """Run a pass every `interval_s` (or sooner when woken) on a daemon thread."""
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="trestle-reaper", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def wake(self) -> None:
        """A waiter found its run's owner lock free: reap now rather than at the next tick."""
        self._wake.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._wake.wait(self.interval_s)
            self._wake.clear()
            if self._stop.is_set():
                return
            try:
                self.pass_once()
            except Exception:  # noqa: BLE001 (a failed pass is retried at the next tick)
                continue

    # -- one pass -------------------------------------------------------------------------------

    def pass_once(self, *, scan_debris: bool = False) -> ReapReport:
        """One reaper pass over `home/live/`. `scan_debris` also lists the month directories for
        `.adm-` debris (the start pass and `trestle recover`)."""
        with self._pass_lock:
            report = ReapReport()
            if self.on_pass is not None:
                self.on_pass()
            finished: list[str] = []
            orphans: list[str] = []
            held: dict[str, int] = {}
            try:
                for run_id in homes.live_run_ids(self.home):
                    report.examined += 1
                    outcome = self._reap_one(run_id, held, report)
                    if outcome == "finished":
                        finished.append(run_id)
                    elif outcome == "orphan":
                        orphans.append(run_id)
                if finished or orphans or scan_debris:
                    self._clean_under_lock(finished, orphans, scan_debris, report)
            finally:
                for fd in held.values():
                    os.close(fd)
            # Problem B: expired key entries whose run is gone leave home/keys/ here, never on a
            # read
            report.keys_purged = idempotency.purge(self.home)
            return report

    def _reap_one(self, run_id: str, held: dict[str, int], report: ReapReport) -> str:
        """`live` (owner alive, or a v0.3.0 run whose leader still runs), `finished` (terminal
        now; its owner lock is in `held` until the marker is gone) or `orphan` (no run behind the
        marker: debris, judged again under the admission lock)."""
        marker = homes.read_marker(self.home, run_id)
        run_dir = homes.marked_run_dir(self.home, run_id, marker)
        if run_dir is None:
            return "orphan"
        fd = homes.try_lock(homes.owner_lock_path(run_dir))
        if fd is None:
            report.left_live += 1
            return "live"
        held[run_id] = fd
        # a takeover re-opens the ledger: seq comes from the rows in memory (rule 2)
        ledger = RunLedger.open(ledger_path(run_dir))
        if ledger.terminal_state() is not None and ledger.has_kind("evidence_finalized"):
            refresh_state(run_dir, ledger)  # the owner may have died before its state.json
            return "finished"
        created = ledger.last_kind("created") or {}
        legacy = "owner" not in created
        if legacy and ledger.has_kind("started"):
            decision = procident.recovery_decision(ledger.records, self.source)
            if decision.branch == "iii":  # a v0.3.0 run whose leader still runs: never stopped
                report.left_live += 1
                os.close(held.pop(run_id))
                return "live"
        recover_run_dir(
            run_dir,
            source=self.source,
            signaller=self.signaller,
            promote=created.get("has_secrets") is False,
        )
        report.reaped.append(run_id)
        return "finished"

    def _clean_under_lock(
        self, finished: list[str], orphans: list[str], scan_debris: bool, report: ReapReport
    ) -> None:
        try:
            with homes.admission_lock(self.home):
                gone = list(finished)
                for run_id in finished:
                    homes.remove_marker(self.home, run_id)
                    report.markers_removed += 1
                for run_id in orphans:
                    # under the lock no admission is in flight: a marker with no run is debris
                    marker = homes.read_marker(self.home, run_id)
                    if homes.marked_run_dir(self.home, run_id, marker) is not None:
                        continue
                    month = (marker or {}).get("month")
                    if isinstance(month, str) and month:
                        staging = self.home / "runs" / month / f"{homes.ADMITTING_PREFIX}{run_id}"
                        if staging.exists():
                            homes.remove_debris_dir(staging)
                            report.debris_removed += 1
                    homes.remove_marker(self.home, run_id)
                    report.markers_removed += 1
                    gone.append(run_id)
                if scan_debris:
                    report.debris_removed += _remove_admission_debris(self.home)
                if gone:
                    # the reaped runs leave the pool: their slots and keys are free now
                    state = pools.load_sched(self.home)
                    for run_id in gone:
                        pools.forget(state, run_id)
                    pools.write_sched(self.home, state)
        except homes.HomeBusy:
            report.busy = True  # tried again at the next pass


def _remove_admission_debris(home: Path) -> int:
    """Every `.adm-` directory under runs/<month>/. Called only under the admission lock, when no
    admission is in flight, so each one is an admission that died."""
    runs_root = home / "runs"
    if not runs_root.is_dir():
        return 0
    removed = 0
    for month in sorted(runs_root.iterdir()):
        if not month.is_dir() or month.name.startswith("."):
            continue
        for entry in sorted(month.iterdir()):
            if entry.name.startswith(homes.ADMITTING_PREFIX):
                homes.remove_debris_dir(entry)
                removed += 1
    return removed


def reap_home(
    home: Path,
    *,
    source: ProcessSource | None = None,
    signaller: Signaller | None = None,
) -> ReapReport:
    """One full pass, debris scan included: what a server's start and `trestle recover` run."""
    return Reaper(home, source=source, signaller=signaller).pass_once(scan_debris=True)
