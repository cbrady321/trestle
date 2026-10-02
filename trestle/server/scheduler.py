"""Kernel-private scheduler — WorkOrder minting after durable created, and the run-capacity
dispatcher (MC-30): `max_running_runs` slots plus a bounded FIFO of `queue_depth` waiting runs."""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from trestle.common import codes
from trestle.common.types import AdmitResultRefused, Handle, RequestOutcome, WorkOrder
from trestle.server import lease
from trestle.server.config import MAX_RUNNING_RUNS_DEFAULT, QUEUE_DEPTH_DEFAULT


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


@dataclass
class Scheduler:
    # every run admitted and not yet completed: waiting, running or not yet enqueued
    queue: deque[Handle] = field(default_factory=deque)
    draining: bool = False
    max_running: int = MAX_RUNNING_RUNS_DEFAULT
    queue_depth: int = QUEUE_DEPTH_DEFAULT
    running: set[Handle] = field(default_factory=set)
    waiting: deque[_Waiting] = field(default_factory=deque)
    # start a dispatched run / finalize a run whose deadline passed while it waited
    on_dispatch: Callable[[WorkOrder], None] | None = None
    on_expire: Callable[[WorkOrder], object] | None = None
    # WR-OWN-8: the environment key each running run holds (capacity 1 per key, L.SL-8.2) and the
    # holder index told when a run's lease ends
    running_keys: dict[Handle, str] = field(default_factory=dict)
    holders: lease.Holders | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

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

    def mint(self, run_id: Handle, snapshot_id: str, spec_hash: str) -> WorkOrder:
        with self._lock:
            self.queue.append(run_id)
        return WorkOrder(run_id=run_id, snapshot_id=snapshot_id, spec_hash=spec_hash)

    def enqueue(self, order: WorkOrder, deadline: float, key: str | None = None) -> Enqueued:
        """Put an admitted run in the FIFO. `deadline` is its admitted deadline on the monotonic
        clock: time spent waiting counts against it, and a run still waiting when it passes is
        finalized `timed_out` without ever being started. `key` is the run's environment key
        (WR-OWN-8): at most one running run holds a key, so a run whose key is held waits behind
        it, first in first out among its key's waiters, while runs of other keys pass it."""
        entry = _Waiting(order=order, deadline=deadline, key=key)
        remaining = deadline - time.monotonic()
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
        complete). A run whose deadline has already passed is not started; a run whose environment
        key a running run holds stays in its place and waits (per-key capacity 1)."""
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
        if self.holders is not None:
            self.holders.release(run_id)  # the lease ends with the run (terminal row or crash)
        self.dispatch()
