"""ControlSurface — Admit + Project composition."""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field
from typing import Any, cast

from trestle.common import codes
from trestle.common.types import (
    AdmitRequest,
    AdmitResult,
    JoinMode,
    PublishView,
    RequestOutcome,
    RunView,
    WorkOrder,
)
from trestle.server.admission import Admission
from trestle.server.conductor import Conductor
from trestle.server.project import Project
from trestle.server.scheduler import Scheduler

COMPLETIONS = frozenset({"bounded", "terminal"})


def _refuse_completion(completion: str, wait_ms: int) -> RequestOutcome | None:
    """`run`'s one additive parameter (MC-16), checked before admission so a refusal is not a run.
    `terminal` waits for the finalized terminal row, so a call that asks not to wait (`wait_ms` of
    zero) contradicts it."""
    if completion not in COMPLETIONS:
        detail = f"invalid completion: {completion!r} (expected bounded or terminal)"
    elif completion == "terminal" and wait_ms <= 0:
        detail = "completion=terminal waits for the terminal row and needs wait_ms above zero"
    else:
        return None
    return RequestOutcome(
        code=codes.INVALID_ARGS, message=detail, retryable=False, origin="admission"
    )


@dataclass
class ControlSurface:
    admission: Admission
    project: Project
    conductor: Conductor
    scheduler: Scheduler
    _admit_lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def _drive_background(self, order: WorkOrder) -> None:
        thread = threading.Thread(
            target=self.conductor.drive,
            args=(order,),
            daemon=True,
        )
        thread.start()

    def run(
        self,
        plugin: str,
        args: dict[str, Any] | None = None,
        version: str | None = None,
        wait_ms: int = 2000,
        idempotency_key: str | None = None,
        completion: str = "bounded",
    ) -> RequestOutcome | RunView:
        refused = _refuse_completion(completion, wait_ms)
        if refused is not None:
            return refused
        self.admission.registry.maybe_refresh()
        result = self._admit_serialized(
            AdmitRequest(
                plugin=plugin,
                args=args or {},
                version=version,
                idempotency_key=idempotency_key,
            )
        )
        if result.tag == "refused":
            return result.outcome

        if result.existing:
            if wait_ms == 0:
                view = self.project.status(result.run_id)
                if isinstance(view, RequestOutcome):
                    return view
                return view
            if completion == "terminal":
                return self.project.await_terminal(result.run_id)
            return self.project.await_one(result.run_id, wait_ms)

        from trestle.server.ledger import RunLedger, ledger_path, run_dir_for

        run_dir = run_dir_for(self.admission.home, result.run_id)
        ledger = RunLedger.open(ledger_path(run_dir))
        created = ledger.last_kind("created")
        spec_hash = str(created.get("spec_hash", "")) if created else ""
        snap = self.admission.registry.get(plugin)
        order = WorkOrder(
            run_id=result.run_id,
            snapshot_id=snap.snapshot_id if snap else "",
            spec_hash=spec_hash,
        )
        self._drive_background(order)

        if wait_ms == 0:
            view = self.project.status(result.run_id)
            if isinstance(view, RequestOutcome):
                return view
            return view
        if completion == "terminal":
            return self.project.await_terminal(result.run_id)
        return self.project.await_one(result.run_id, wait_ms)

    async def run_async(
        self,
        plugin: str,
        args: dict[str, Any] | None = None,
        version: str | None = None,
        wait_ms: int = 2000,
        idempotency_key: str | None = None,
        completion: str = "bounded",
    ) -> RequestOutcome | RunView:
        refused = _refuse_completion(completion, wait_ms)
        if refused is not None:
            return refused
        # The registry refresh stays inline until L.CL-A1.1 moves it into the admission lane
        # (it flips G-A4); admission itself runs on a worker thread, so a slow admit does not
        # stop the loop answering other calls (L.CS-4.2, until CL-A1's lane replaces this).
        self.admission.registry.maybe_refresh()
        result = await asyncio.to_thread(
            self._admit_serialized,
            AdmitRequest(
                plugin=plugin,
                args=args or {},
                version=version,
                idempotency_key=idempotency_key,
            ),
        )
        if result.tag == "refused":
            return result.outcome

        if result.existing:
            if wait_ms == 0:
                return await asyncio.to_thread(self.project.status, result.run_id)
            if completion == "terminal":
                return await self.project.await_terminal_async(result.run_id)
            return await self.project.await_one_async(result.run_id, wait_ms)

        order = await asyncio.to_thread(self._work_order, result.run_id, plugin)
        asyncio.create_task(self.conductor.drive_async(order))

        if wait_ms == 0:
            return await asyncio.to_thread(self.project.status, result.run_id)
        if completion == "terminal":
            return await self.project.await_terminal_async(result.run_id)
        return await self.project.await_one_async(result.run_id, wait_ms)

    def _admit_serialized(self, request: AdmitRequest) -> AdmitResult:
        """One admit at a time: what the event loop's single thread gave every caller before
        admission moved to a worker (an idempotency key checked twice at once would mint two
        runs). CL-A1's admission lane replaces this."""
        with self._admit_lock:
            return self.admission.admit(request)

    def _work_order(self, run_id: str, plugin: str) -> WorkOrder:
        from trestle.server.ledger import RunLedger, ledger_path, run_dir_for

        run_dir = run_dir_for(self.admission.home, run_id)
        created = RunLedger.open(ledger_path(run_dir)).last_kind("created")
        spec_hash = str(created.get("spec_hash", "")) if created else ""
        snap = self.admission.registry.get(plugin)
        return WorkOrder(
            run_id=run_id,
            snapshot_id=snap.snapshot_id if snap else "",
            spec_hash=spec_hash,
        )

    def await_runs(
        self,
        run_ids: list[str],
        mode: str = "all",
        timeout_ms: int = 2000,
    ) -> list[RunView] | RequestOutcome:
        if mode not in {"all", "any", "first_failure"}:
            return RequestOutcome(
                code=codes.PROJECTION_INVALID_ARGS,
                message=f"invalid join mode: {mode}",
                retryable=False,
                origin="projection",
            )
        return self.project.await_many(run_ids, cast(JoinMode, mode), timeout_ms)

    async def await_runs_async(
        self,
        run_ids: list[str],
        mode: str = "all",
        timeout_ms: int = 2000,
    ) -> list[RunView] | RequestOutcome:
        if mode not in {"all", "any", "first_failure"}:
            return RequestOutcome(
                code=codes.PROJECTION_INVALID_ARGS,
                message=f"invalid join mode: {mode}",
                retryable=False,
                origin="projection",
            )
        return await self.project.await_many_async(run_ids, cast(JoinMode, mode), timeout_ms)

    def cancel(self, run_id: str) -> RequestOutcome:
        return self.project.cancel(run_id)

    def query(
        self,
        view: str,
        params: dict[str, Any] | None = None,
        cursor: str | None = None,
    ) -> dict[str, Any] | RequestOutcome:
        out = self.project.query(view, params or {}, cursor)
        if isinstance(out, RequestOutcome):
            return out
        return out

    def fetch(self, target: str, window: dict[str, Any]) -> dict[str, Any] | RequestOutcome:
        out = self.project.fetch(target, window)
        if isinstance(out, RequestOutcome):
            return out
        return out

    def pin(self, target: str) -> RequestOutcome:
        return self.project.pin(target)

    def unpin(self, target: str) -> RequestOutcome:
        return self.project.unpin(target)

    def list_plugins(self) -> dict[str, Any]:
        return self.project.list_plugins().to_dict()

    def describe_plugin(self, plugin_id: str) -> dict[str, Any] | RequestOutcome:
        out = self.project.describe_plugin(plugin_id)
        if isinstance(out, RequestOutcome):
            return out
        return out

    def publish_plugin(
        self,
        source: str,
        *,
        name: str | None = None,
    ) -> PublishView | RequestOutcome:
        return self.project.publish_plugin(source, name=name)
