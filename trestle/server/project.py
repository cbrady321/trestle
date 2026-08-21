"""Project port — bounded pictures and agent verbs."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from trestle.common import codes
from trestle.common.types import CatalogView, Handle, JoinMode, RequestOutcome, RunView
from trestle.query.fs import FilesystemQueryBackend
from trestle.server.ledger import TERMINAL_KINDS, RunLedger, evidence_dir, ledger_path
from trestle.server.pins import PinStore
from trestle.server.projection import (
    build_summary,
    fetch_bytes,
    load_index,
    write_summary_json,
)
from trestle.server.registry import Registry
from trestle.server.runs import RunRegistry


@dataclass
class Project:
    home: Path
    registry: Registry
    run_registry: RunRegistry

    def status(self, run_id: Handle) -> RunView | RequestOutcome:
        ledger = self._ledger_for(run_id)
        if ledger is None:
            return RequestOutcome(
                code=codes.INVALID_HANDLE,
                message=f"unknown run: {run_id}",
                retryable=False,
                origin="projection",
            )
        return self._run_view(ledger, run_id)

    def await_one(self, run_id: Handle, wait_ms: int) -> RunView | RequestOutcome:
        deadline = time.monotonic() + (wait_ms / 1000.0)
        while True:
            view = self.status(run_id)
            if isinstance(view, RequestOutcome):
                return view
            if view.state not in {"queued", "running"}:
                return view
            if time.monotonic() >= deadline:
                return view
            time.sleep(0.05)

    def await_many(
        self,
        run_ids: list[Handle],
        mode: JoinMode,
        timeout_ms: int,
    ) -> list[RunView] | RequestOutcome:
        deadline = time.monotonic() + (timeout_ms / 1000.0)
        while True:
            views, outcome = self._collect_run_views(run_ids)
            if outcome is not None:
                return outcome
            assert views is not None
            if _join_satisfied(views, mode):
                return views
            if time.monotonic() >= deadline:
                return views
            time.sleep(0.05)

    def _collect_run_views(
        self,
        run_ids: list[Handle],
    ) -> tuple[list[RunView] | None, RequestOutcome | None]:
        views: list[RunView] = []
        for run_id in run_ids:
            view = self.status(run_id)
            if isinstance(view, RequestOutcome):
                return None, view
            views.append(view)
        return views, None

    def cancel(self, run_id: Handle) -> RequestOutcome:
        ledger = self._ledger_for(run_id)
        if ledger is None:
            return RequestOutcome(
                code=codes.INVALID_HANDLE,
                message=f"unknown run: {run_id}",
                retryable=False,
                origin="projection",
            )

        state = ledger.projected_state()
        if state not in {"queued", "running"}:
            return RequestOutcome(
                code=codes.INVALID_HANDLE,
                message=f"run not cancellable: {run_id} ({state})",
                retryable=False,
                origin="projection",
            )

        run_dir = self._run_dir_for(run_id)
        if run_dir is None:
            return RequestOutcome(
                code=codes.INVALID_HANDLE,
                message=f"unknown run: {run_id}",
                retryable=False,
                origin="projection",
            )

        self.run_registry.request_cancel(run_id, run_dir)
        return RequestOutcome(
            code=codes.CANCEL_ACCEPTED,
            message=f"cancel accepted for {run_id}",
            retryable=False,
            origin="projection",
        )

    def query(
        self,
        view: str,
        params: dict[str, object],
        cursor: Handle | None = None,
    ) -> dict[str, object] | RequestOutcome:
        return FilesystemQueryBackend(self.home).query(view, params, cursor)

    def fetch(
        self,
        target: Handle,
        window: dict[str, object],
    ) -> dict[str, object] | RequestOutcome:
        return fetch_bytes(home=self.home, target=target, window=window)

    def pin(self, target: Handle) -> RequestOutcome:
        if target.startswith("/") or ".." in target:
            return RequestOutcome(
                code=codes.INVALID_HANDLE,
                message=f"invalid pin target: {target}",
                retryable=False,
                origin="projection",
            )
        PinStore.open(self.home).pin(target)
        return RequestOutcome(
            code=codes.PIN_ACCEPTED,
            message=f"pinned {target}",
            retryable=False,
            origin="projection",
        )

    def unpin(self, target: Handle) -> RequestOutcome:
        if target.startswith("/") or ".." in target:
            return RequestOutcome(
                code=codes.INVALID_HANDLE,
                message=f"invalid unpin target: {target}",
                retryable=False,
                origin="projection",
            )
        PinStore.open(self.home).unpin(target)
        return RequestOutcome(
            code=codes.UNPIN_ACCEPTED,
            message=f"unpinned {target}",
            retryable=False,
            origin="projection",
        )

    def list_plugins(self) -> CatalogView:
        return self.registry.catalog()

    def describe_plugin(self, plugin_id: str) -> dict[str, object] | RequestOutcome:
        desc = self.registry.describe(plugin_id)
        if desc is None:
            return RequestOutcome(
                code=codes.NOT_FOUND,
                message=f"plugin not found: {plugin_id}",
                retryable=False,
                origin="projection",
            )
        return desc

    def _ledger_for(self, run_id: Handle) -> RunLedger | None:
        runs_root = self.home / "runs"
        if not runs_root.exists():
            return None
        for month_dir in runs_root.iterdir():
            run_dir = month_dir / run_id
            path = ledger_path(run_dir)
            if path.exists():
                return RunLedger.open(path)
        return None

    def _run_dir_for(self, run_id: Handle) -> Path | None:
        runs_root = self.home / "runs"
        if not runs_root.exists():
            return None
        for month_dir in runs_root.iterdir():
            run_dir = month_dir / run_id
            if ledger_path(run_dir).exists():
                return run_dir
        return None

    def _run_view(self, ledger: RunLedger, run_id: Handle) -> RunView:
        state = ledger.projected_state()

        run_dir = self._run_dir_for(run_id)
        evidence = evidence_dir(run_dir) if run_dir is not None else None
        meta: dict[str, object] = {}
        if evidence is not None:
            meta_path = evidence / "meta.json"
            if meta_path.exists():
                meta = json.loads(meta_path.read_text(encoding="utf-8"))

        duration_ms: int | None = None
        ended = ledger.last_kind("execution_ended")
        meta_duration = meta.get("duration_ms")
        if ended is not None:
            raw_duration = ended.get("duration_ms")
            if isinstance(raw_duration, int):
                duration_ms = raw_duration
        elif isinstance(meta_duration, int):
            duration_ms = meta_duration

        limits_exceeded = meta.get("limits_exceeded")
        if limits_exceeded is None:
            limit_record = ledger.last_kind("limit_exceeded")
            if limit_record is not None:
                raw = limit_record.get("markers")
                if isinstance(raw, list):
                    limits_exceeded = raw

        raw_artifact_count = meta.get("artifact_count", 0)
        artifact_count = raw_artifact_count if isinstance(raw_artifact_count, int) else 0
        if artifact_count == 0:
            artifact_count = sum(
                1 for record in ledger.records if record.get("kind") == "artifact_available"
            )

        event_count = 0
        if evidence is not None:
            events_path = evidence / "events.ndjson"
            if events_path.exists():
                event_count = sum(
                    1
                    for line in events_path.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                )

        summary = None
        truncated = False
        omitted: list[str] | None = None
        next_handle: str | None = None
        result_bytes: int | None = None

        if state not in {"queued", "running"} and evidence is not None:
            index = load_index(evidence)
            result_path = evidence / "result.json"
            if index is not None and result_path.exists():
                budget = self._summary_budget(ledger)
                projection = build_summary(
                    run_id=run_id,
                    result_path=result_path,
                    index=index,
                    budget=budget,
                )
                summary = projection.summary
                truncated = projection.truncated
                omitted = projection.omitted
                next_handle = projection.next_handle
                result_bytes = projection.result_bytes
                write_summary_json(evidence, projection, budget)
            elif evidence.joinpath("result.state").exists():
                result_bytes = None
                truncated = True

        return RunView(
            run_id=run_id,
            state=state,
            duration_ms=duration_ms,
            event_count=event_count,
            artifact_count=artifact_count,
            result_bytes=result_bytes,
            truncated=truncated,
            omitted=omitted,
            summary=summary,
            next=next_handle,
            limits_exceeded=limits_exceeded if isinstance(limits_exceeded, list) else None,
        )

    def _summary_budget(self, ledger: RunLedger) -> int:
        created = ledger.last_kind("created")
        plugin = str(created.get("plugin", "")) if created else ""
        snap = self.registry.get(plugin)
        if snap is not None:
            return snap.summary_budget
        return 4096


_NON_TERMINAL_STATES = frozenset({"queued", "running"})
_FAILURE_TERMINAL_STATES = TERMINAL_KINDS - frozenset({"succeeded"})


def _is_non_terminal(state: str) -> bool:
    return state in _NON_TERMINAL_STATES


def _join_satisfied(views: list[RunView], mode: JoinMode) -> bool:
    if mode == "all":
        return all(not _is_non_terminal(view.state) for view in views)
    if mode == "any":
        return any(not _is_non_terminal(view.state) for view in views)
    if mode == "first_failure":
        if any(view.state in _FAILURE_TERMINAL_STATES for view in views):
            return True
        return all(not _is_non_terminal(view.state) for view in views)
    return False
