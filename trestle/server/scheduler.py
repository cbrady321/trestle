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
        finalized `timed_out` without ever being started."""
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
        complete). A run whose deadline has already passed is not started."""
        start: list[WorkOrder] = []
        expired: list[WorkOrder] = []
        with self._lock:
            while self.waiting and len(self.running) < self.max_running:
                entry = self.waiting.popleft()
                if entry.timer is not None:
                    entry.timer.cancel()
                if entry.deadline <= time.monotonic():
                    expired.append(entry.order)
                    continue
                self.running.add(entry.order.run_id)
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
        self.dispatch()
