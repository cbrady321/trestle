"""Kernel-private scheduler — WorkOrder minting after durable created."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from trestle.common import codes
from trestle.common.types import AdmitResultRefused, Handle, RequestOutcome, WorkOrder

QUEUE_DEPTH = 256


@dataclass
class Scheduler:
    queue: deque[Handle] = field(default_factory=deque)
    draining: bool = False

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
        if len(self.queue) >= QUEUE_DEPTH:
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
        self.queue.append(run_id)
        return WorkOrder(run_id=run_id, snapshot_id=snapshot_id, spec_hash=spec_hash)

    def complete(self, run_id: Handle) -> None:
        try:
            self.queue.remove(run_id)
        except ValueError:
            pass
