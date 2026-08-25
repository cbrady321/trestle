"""ControlSurface — Admit + Project composition."""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from typing import Any, cast

from trestle.common import codes
from trestle.common.types import AdmitRequest, JoinMode, RequestOutcome, RunView, WorkOrder
from trestle.server.admission import Admission
from trestle.server.conductor import Conductor
from trestle.server.project import Project
from trestle.server.scheduler import Scheduler


@dataclass
class ControlSurface:
    admission: Admission
    project: Project
    conductor: Conductor
    scheduler: Scheduler

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
    ) -> RequestOutcome | RunView:
        self.admission.registry.maybe_refresh()
        result = self.admission.admit(
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
        return self.project.await_one(result.run_id, wait_ms)

    async def run_async(
        self,
        plugin: str,
        args: dict[str, Any] | None = None,
        version: str | None = None,
        wait_ms: int = 2000,
        idempotency_key: str | None = None,
    ) -> RequestOutcome | RunView:
        self.admission.registry.maybe_refresh()
        result = self.admission.admit(
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
            return await self.project.await_one_async(result.run_id, wait_ms)

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
        asyncio.create_task(self.conductor.drive_async(order))

        if wait_ms == 0:
            view = self.project.status(result.run_id)
            if isinstance(view, RequestOutcome):
                return view
            return view
        return await self.project.await_one_async(result.run_id, wait_ms)

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
