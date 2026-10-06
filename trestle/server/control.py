"""ControlSurface — Admit + Project composition."""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import Future, ThreadPoolExecutor
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
from trestle.server.home import read_marker
from trestle.server.pool import epoch
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


class AdmissionLane:
    """MC-30: the one admission thread. The registry refresh (validation of dropped-in plugins, a
    subprocess import) and `Admission.admit` both run here, one job at a time, so nothing blocks the
    event loop and admission stays serialized (the BFD-29 condition: an idempotency key is checked
    once, never twice at once)."""

    THREAD_NAME = "trestle-admission"

    def __init__(self, admission: Admission) -> None:
        self._admission = admission
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix=self.THREAD_NAME)

    def submit_admit(self, request: AdmitRequest) -> Future[AdmitResult]:
        return self._pool.submit(self._refresh_then_admit, request)

    def submit_refresh(self) -> Future[None]:
        return self._pool.submit(self._admission.registry.maybe_refresh)

    def _refresh_then_admit(self, request: AdmitRequest) -> AdmitResult:
        self._admission.registry.maybe_refresh()
        return self._admission.admit(request)

    def close(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)


@dataclass
class ControlSurface:
    admission: Admission
    project: Project
    conductor: Conductor
    scheduler: Scheduler
    lane: AdmissionLane = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        self.lane = AdmissionLane(self.admission)
        self.scheduler.on_dispatch = self._start
        self.scheduler.on_expire = self.conductor.finalize_unspawned
        # v0.4: a cancel flag another server wrote for a run waiting here, seen on the 250 ms pass
        self.scheduler.on_cancel = self.conductor.cancel_flag_written

    def submit_admit(self, request: AdmitRequest) -> Future[AdmitResult]:
        """MC-30: refresh the registry, then admit, on the admission thread."""
        return self.lane.submit_admit(request)

    def submit_refresh(self) -> Future[None]:
        """The registry refresh alone (the `tools/list` hook), on the admission thread."""
        return self.lane.submit_refresh()

    def _drive_background(self, order: WorkOrder) -> None:
        """Hand an admitted run to the dispatcher: it starts now if a slot is free, else it waits
        in the FIFO with its admitted deadline still running (MC-30, B2-C5)."""
        # the run's environment key, arrival and deadline, as admission recorded them in its live
        # marker (the pool orders a key's waiters across servers by arrival, rule 6)
        marker = read_marker(self.admission.home, order.run_id) or {}
        key = marker.get("lease_key")
        deadline = marker.get("deadline")
        self.scheduler.enqueue(
            order,
            self.conductor.admitted_deadline(order),
            key=key if isinstance(key, str) else None,
            arrival=epoch(marker.get("arrival")) if "arrival" in marker else None,
            deadline_epoch=float(deadline) if isinstance(deadline, int | float) else None,
        )

    def _start(self, order: WorkOrder) -> None:
        threading.Thread(target=self.conductor.drive, args=(order,), daemon=True).start()

    def run(
        self,
        plugin: str,
        args: dict[str, Any] | None = None,
        version: str | None = None,
        wait_ms: int = 2000,
        idempotency_key: str | None = None,
        completion: str = "bounded",
        caller_session: str | None = None,
    ) -> RequestOutcome | RunView:
        refused = _refuse_completion(completion, wait_ms)
        if refused is not None:
            return refused
        result = self.submit_admit(
            AdmitRequest(
                plugin=plugin,
                args=args or {},
                version=version,
                idempotency_key=idempotency_key,
                caller_session=caller_session,
            )
        ).result()
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
            secrets=result.secrets,
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
        caller_session: str | None = None,
    ) -> RequestOutcome | RunView:
        refused = _refuse_completion(completion, wait_ms)
        if refused is not None:
            return refused
        # The registry refresh and the admit both run on the admission thread (MC-30), so a slow
        # plugin probe or admit never stops the loop answering other calls (G-A4).
        admitting = self.submit_admit(
            AdmitRequest(
                plugin=plugin,
                args=args or {},
                version=version,
                idempotency_key=idempotency_key,
                caller_session=caller_session,
            )
        )
        try:
            result = await asyncio.wrap_future(admitting)
        except asyncio.CancelledError:
            # The caller went away (a cancel notification, WR-TERM-6). An admit not yet started is
            # withdrawn and no run exists; one already started creates its run, which is driven
            # all the same: a severed call never leaves an admitted run that nothing starts.
            if not admitting.cancel():
                admitting.add_done_callback(lambda done: self._hand_off_admitted(done, plugin))
            raise
        if result.tag == "refused":
            return result.outcome

        if result.existing:
            if wait_ms == 0:
                return await asyncio.to_thread(self.project.status, result.run_id)
            if completion == "terminal":
                return await self.project.await_terminal_async(result.run_id)
            return await self.project.await_one_async(result.run_id, wait_ms)

        # Shielded: once admitted, the run is handed to the dispatcher even if the caller goes away
        # while the hand-off is under way.
        await asyncio.shield(
            asyncio.to_thread(self._hand_off, result.run_id, plugin, result.secrets)
        )

        if wait_ms == 0:
            return await asyncio.to_thread(self.project.status, result.run_id)
        if completion == "terminal":
            return await self.project.await_terminal_async(result.run_id)
        return await self.project.await_one_async(result.run_id, wait_ms)

    def _hand_off(self, run_id: str, plugin: str, secrets: dict[str, Any] | None = None) -> None:
        """Hand a just-admitted run to the dispatcher (its work order, then the FIFO)."""
        self._drive_background(self._work_order(run_id, plugin, secrets))

    def _hand_off_admitted(self, admitted: Future[AdmitResult], plugin: str) -> None:
        """The hand-off of an admit whose caller went away mid-admission: a new run is driven; a
        refusal, a failed admit or an existing run needs nothing."""
        if admitted.cancelled() or admitted.exception() is not None:
            return
        result = admitted.result()
        if result.tag != "refused" and not result.existing:
            self._hand_off(result.run_id, plugin, result.secrets)

    def _work_order(
        self, run_id: str, plugin: str, secrets: dict[str, Any] | None = None
    ) -> WorkOrder:
        from trestle.server.ledger import RunLedger, ledger_path, run_dir_for

        run_dir = run_dir_for(self.admission.home, run_id)
        created = RunLedger.open(ledger_path(run_dir)).last_kind("created")
        spec_hash = str(created.get("spec_hash", "")) if created else ""
        snap = self.admission.registry.get(plugin)
        return WorkOrder(
            run_id=run_id,
            snapshot_id=snap.snapshot_id if snap else "",
            spec_hash=spec_hash,
            secrets=secrets or {},
        )

    def await_runs(
        self,
        run_ids: list[str],
        mode: str = "all",
        timeout_ms: int = 2000,
        caller_session: str | None = None,
    ) -> list[RunView] | RequestOutcome:
        if mode not in {"all", "any", "first_failure"}:
            return RequestOutcome(
                code=codes.PROJECTION_INVALID_ARGS,
                message=f"invalid join mode: {mode}",
                retryable=False,
                origin="projection",
            )
        return self.project.await_many(
            run_ids, cast(JoinMode, mode), timeout_ms, caller_session=caller_session
        )

    async def await_runs_async(
        self,
        run_ids: list[str],
        mode: str = "all",
        timeout_ms: int = 2000,
        caller_session: str | None = None,
    ) -> list[RunView] | RequestOutcome:
        if mode not in {"all", "any", "first_failure"}:
            return RequestOutcome(
                code=codes.PROJECTION_INVALID_ARGS,
                message=f"invalid join mode: {mode}",
                retryable=False,
                origin="projection",
            )
        return await self.project.await_many_async(
            run_ids, cast(JoinMode, mode), timeout_ms, caller_session=caller_session
        )

    def cancel(self, run_id: str, caller_session: str | None = None) -> RequestOutcome:
        return self.project.cancel(run_id, caller_session=caller_session)

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
