"""Kernel-private scheduler — WorkOrder minting after durable created, and the run-capacity
dispatcher (MC-30): `max_running_runs` slots plus a bounded FIFO of `queue_depth` waiting runs.

v0.4 (Problem A, rules 4 and 5): each server keeps its own FIFO in memory, bounded per server
(`queue_depth`, and `max_held_runs` for Feature 3's held runs), while the slots are one pool per
home (`trestle.server.pool`, `home/sched.json`). A kernel's scheduler is given its `pool`; one
without (a unit test's) counts its own `max_running` slots in memory, as before."""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from trestle.common import codes
from trestle.common.types import AdmitResultRefused, Handle, RequestOutcome, WorkOrder
from trestle.server import pool as pools
from trestle.server.config import (
    MAX_HELD_RUNS_DEFAULT,
    MAX_RUNNING_RUNS_DEFAULT,
    QUEUE_DEPTH_DEFAULT,
    load_config,
)
from trestle.server.home import HomeBusy, admission_lock


@dataclass(frozen=True)
class Enqueued:
    """`enqueue`'s answer: `queued` (the run holds a slot or waits for one, its deadline running
    from admission) or `refused` with the code that says why (SL-8 adds the per-key dimension
    behind this same surface)."""

    tag: Literal["queued", "refused"]
    code: str | None = None


@dataclass
class _Waiting:
    order: WorkOrder
    deadline: float  # the admitted deadline on the monotonic clock (B2-C5)
    key: str | None
    timer: threading.Timer | None = None
    # what the home's pool orders and records by: created.at and the deadline, epoch seconds
    arrival: float = 0.0
    deadline_epoch: float = 0.0


@dataclass
class Scheduler:
    # every run admitted and not yet completed: waiting, running or not yet enqueued
    queue: deque[Handle] = field(default_factory=deque)
    draining: bool = False
    max_running: int = MAX_RUNNING_RUNS_DEFAULT
    queue_depth: int = QUEUE_DEPTH_DEFAULT
    # Feature 3's seam: held runs sit outside `queue_depth`, bounded by `max_held` (per server,
    # read at start); `held` is the in-memory count. Nothing is held until step 6.
    max_held: int = MAX_HELD_RUNS_DEFAULT
    held: set[Handle] = field(default_factory=set)
    running: set[Handle] = field(default_factory=set)
    waiting: deque[_Waiting] = field(default_factory=deque)
    # start a dispatched run / finalize a run whose deadline passed while it waited
    on_dispatch: Callable[[WorkOrder], None] | None = None
    on_expire: Callable[[WorkOrder], object] | None = None
    # WR-OWN-8: the environment key each running run of this server holds (capacity 1 per key
    # home-wide, L.SL-8.2; across servers through the pool)
    running_keys: dict[Handle, str] = field(default_factory=dict)
    # v0.4: the home's slot pool (rules 5 and 6); None counts this scheduler's own slots
    pool: pools.Pool | None = None
    # v0.4: told once a run is done here (after its terminal row, or its drive raised): the
    # owner removes the run's live marker, frees its slot and closes its owner lock
    # (`Ownership.release`). Called outside `_lock`: the home's admission lock is never taken while
    # holding it (rule 10).
    on_complete: Callable[[Handle], None] | None = None
    # v0.4: a cancel flag found on a waiting run by the 250 ms pass (any server's `cancel` writes
    # it); the conductor finalizes the run as cancelled while queued
    on_cancel: Callable[[Handle], None] | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)
    _pass_thread: threading.Thread | None = field(default=None, repr=False, compare=False)

    def check_admit_capacity(self) -> AdmitResultRefused | None:
        if self.draining:
            return AdmitResultRefused(
                tag="refused",
                outcome=RequestOutcome(
                    code=codes.SERVICE_DRAINING,
                    message="service is draining",
                    retryable=True,
                    origin="admission",
                ),
            )
        # slots plus the bounded FIFO: a run beyond both is refused before it has a run dir
        if len(self.queue) >= self.max_running + self.queue_depth:
            return AdmitResultRefused(
                tag="refused",
                outcome=RequestOutcome(
                    code=codes.QUEUE_FULL,
                    message="admission queue full",
                    retryable=True,
                    origin="admission",
                ),
            )
        return None

    def check_hold_capacity(self) -> AdmitResultRefused | None:
        """Feature 3's per-server bound on held runs (`[operator] max_held_runs`), counted in
        memory; held runs do not count against `queue_depth`."""
        if len(self.held) < self.max_held:
            return None
        return AdmitResultRefused(
            tag="refused",
            outcome=RequestOutcome(
                code=codes.QUEUE_FULL,
                message="held runs full",
                retryable=True,
                origin="admission",
            ),
        )

    def mint(self, run_id: Handle, snapshot_id: str, spec_hash: str) -> WorkOrder:
        with self._lock:
            self.queue.append(run_id)
        return WorkOrder(run_id=run_id, snapshot_id=snapshot_id, spec_hash=spec_hash)

    def enqueue(
        self,
        order: WorkOrder,
        deadline: float,
        key: str | None = None,
        *,
        arrival: float | None = None,
        deadline_epoch: float | None = None,
    ) -> Enqueued:
        """Put an admitted run in the FIFO. `deadline` is its admitted deadline on the monotonic
        clock: time spent waiting counts against it, and a run still waiting when it passes is
        finalized `timed_out` without ever being started. `key` is the run's environment key
        (WR-OWN-8): at most one running run holds a key, so a run whose key is held waits behind
        it, first in first out among its key's waiters, while runs of other keys pass it. With a
        pool, `arrival` (created.at) orders a key's waiters across servers and `deadline_epoch` is
        what `home/sched.json` records (both from the run's live marker)."""
        remaining = deadline - time.monotonic()
        now = time.time()
        entry = _Waiting(
            order=order,
            deadline=deadline,
            key=key,
            arrival=now if arrival is None else arrival,
            deadline_epoch=now + remaining if deadline_epoch is None else deadline_epoch,
        )
        with self._lock:
            self.waiting.append(entry)
            if remaining > 0:
                entry.timer = threading.Timer(remaining, self._expire_waiting, [order.run_id])
                entry.timer.daemon = True
                entry.timer.start()
        if remaining <= 0:
            self._expire_waiting(order.run_id)
        self.dispatch()
        return Enqueued(tag="queued")

    def dispatch(self) -> None:
        """Start waiting runs, oldest first, while a slot is free (called on enqueue and on
        complete, and with a pool every 250 ms while runs wait). A run whose deadline has already
        passed is not started; a run whose environment key a running run holds stays in its place
        and waits (per-key capacity 1)."""
        if self.pool is not None:
            self._dispatch_pooled()
            return
        start: list[WorkOrder] = []
        expired: list[WorkOrder] = []
        with self._lock:
            held = set(self.running_keys.values())
            for entry in list(self.waiting):
                if len(self.running) >= self.max_running:
                    break
                if entry.deadline <= time.monotonic():
                    self.waiting.remove(entry)
                    if entry.timer is not None:
                        entry.timer.cancel()
                    expired.append(entry.order)
                    continue
                if entry.key is not None and entry.key in held:
                    continue
                self.waiting.remove(entry)
                if entry.timer is not None:
                    entry.timer.cancel()
                self.running.add(entry.order.run_id)
                if entry.key is not None:
                    self.running_keys[entry.order.run_id] = entry.key
                    held.add(entry.key)
                start.append(entry.order)
        for order in expired:
            self._finalize_expired(order)
        for order in start:
            if self.on_dispatch is not None:
                self.on_dispatch(order)

    # -- the home's pool (v0.4 rules 5 to 7) ----------------------------------------------------

    def seen(self) -> None:
        """The reaper's pass (every 10 s): this server's `seen_at` (rule 5's reserve) in a locked
        step that also grants, as any pass does."""
        if self.pool is not None:
            self._dispatch_pooled(seen=True)

    def _dispatch_pooled(self, *, seen: bool = False) -> None:
        expired: list[WorkOrder] = []
        with self._lock:
            now = time.monotonic()
            for entry in [w for w in self.waiting if w.deadline <= now]:
                self.waiting.remove(entry)
                if entry.timer is not None:
                    entry.timer.cancel()
                expired.append(entry.order)
        for order in expired:
            self._finalize_expired(order)
        start: list[WorkOrder] = []
        if seen or self._wants_lock():
            assert self.pool is not None
            try:
                with admission_lock(self.pool.home):
                    start = self._locked_step(seen=seen)
            except HomeBusy:
                pass  # tried again at the next pass
        for order in start:
            if self.on_dispatch is not None:
                self.on_dispatch(order)
        self._ensure_pass()

    def _wants_lock(self) -> bool:
        """Rule 5 (When): the lock-free pre-read. Take the lock only when a waiting run could
        start and the file shows a free slot, or this server's own `last_pass_at` is over 500 ms
        old (its candidacy lapses at 1 s), or a completion is still to be recorded."""
        assert self.pool is not None
        with self._lock:
            keys = [entry.key for entry in self.waiting]
        if self.pool.has_pending():
            return True
        if not keys:
            return False
        state = self.pool.pre_read()
        if state is None:
            return True
        row = state["servers"].get(self.pool.server_id)
        if row is None or time.time() - float(row.get("last_pass_at") or 0.0) > pools.PASS_STALE_S:
            return True
        held = pools.running_keys(state)
        startable = any(key is None or key not in held for key in keys)
        return startable and pools.used_slots(state) < self.max_running

    def _locked_step(self, *, seen: bool) -> list[WorkOrder]:
        """Rule 7 steps 1 and 5 to 7 under the admission lock: read the pool (dead servers
        dropped, pending completions applied) and the pool's settings from config.toml; record
        this server's waiting runs; grant slots by rules 5 and 6, flipping each granted run's
        marker to running; write `home/sched.json`. The granted runs are started by the caller,
        after the lock."""
        assert self.pool is not None
        pool = self.pool
        state = pool.load()
        max_share: int | None = None
        try:
            cfg = load_config(pool.home)
            self.max_running = cfg.max_running_runs
            max_share = cfg.max_share
        except (OSError, ValueError):
            pass  # a config.toml being rewritten: last values
        now = time.time()
        start: list[WorkOrder] = []
        with self._lock:
            wants = [
                pools.Want(
                    run_id=entry.order.run_id,
                    key=entry.key,
                    arrival=entry.arrival,
                    deadline=entry.deadline_epoch,
                )
                for entry in self.waiting
            ]
            waiting_ids = {want.run_id for want in wants}
            unqueued = {
                run_id
                for run_id in self.queue
                if run_id not in self.running
                and run_id not in waiting_ids
                and run_id not in self.held
            }
            row = pools.sync_own(state, pool.server_id, wants, unqueued, draining=self.draining)
            row["seen_at" if seen else "last_pass_at"] = now
            granted = pools.choose_grants(
                state,
                pool.server_id,
                wants,
                max_running=self.max_running,
                max_share=max_share,
                now=now,
            )
            chosen = {want.run_id for want in granted}
            for entry in [w for w in self.waiting if w.order.run_id in chosen]:
                self.waiting.remove(entry)
                if entry.timer is not None:
                    entry.timer.cancel()
                self.running.add(entry.order.run_id)
                if entry.key is not None:
                    self.running_keys[entry.order.run_id] = entry.key
                start.append(entry.order)
        for order in start:
            pools.flip_marker(pool.home, order.run_id, "running")
        pool.write(state)
        return start

    def _ensure_pass(self) -> None:
        """Rule 5 (When): a pass every 250 ms while this server has waiting (or held) runs."""
        if self.pool is None:
            return
        with self._lock:
            if self._pass_thread is not None or not (self.waiting or self.held):
                return
            thread = threading.Thread(target=self._pass_loop, name="trestle-pass", daemon=True)
            self._pass_thread = thread
        thread.start()

    def _pass_loop(self) -> None:
        assert self.pool is not None
        while True:
            time.sleep(pools.PASS_INTERVAL_S)
            with self._lock:
                if not (self.waiting or self.held):
                    self._pass_thread = None
                    return
                run_ids = [entry.order.run_id for entry in self.waiting] + sorted(self.held)
            try:
                # a cancel of a queued (or held) run, written by any server, is seen here
                if self.on_cancel is not None:
                    for run_id in run_ids:
                        if self.pool.cancel_requested(run_id):
                            self.on_cancel(run_id)
                self.dispatch()
            except Exception:  # noqa: BLE001 (a failed pass is retried at the next one)
                continue

    def _expire_waiting(self, run_id: Handle) -> None:
        with self._lock:
            entry = next((w for w in self.waiting if w.order.run_id == run_id), None)
            if entry is None:  # dispatched (or expired) first
                return
            self.waiting.remove(entry)
        self._finalize_expired(entry.order)

    def cancel_waiting(self, run_id: Handle, finalize: Callable[[WorkOrder], object]) -> bool:
        """A cancel reached a run still waiting in the FIFO: take it out and finalize it through
        `finalize` (the conductor's queued path, B2-C12) with no process and no lane. False when
        the run is not waiting (running, dispatched first, or not yet enqueued): the conductor
        sees its cancel flag then."""
        with self._lock:
            entry = next((w for w in self.waiting if w.order.run_id == run_id), None)
            if entry is None:
                return False
            self.waiting.remove(entry)
            if entry.timer is not None:
                entry.timer.cancel()
        try:
            finalize(entry.order)
        finally:
            self.complete(run_id)
        return True

    def _finalize_expired(self, order: WorkOrder) -> None:
        try:
            if self.on_expire is not None:
                self.on_expire(order)
        finally:
            self.complete(order.run_id)

    def complete(self, run_id: Handle) -> None:
        with self._lock:
            try:
                self.queue.remove(run_id)
            except ValueError:
                pass
            self.running.discard(run_id)
            self.running_keys.pop(run_id, None)
        if self.pool is not None:
            self.pool.completed(run_id)  # its slot and key go at the completion's locked step
        if self.on_complete is not None:
            self.on_complete(run_id)
        self.dispatch()
