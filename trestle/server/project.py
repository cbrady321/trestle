"""Project port — bounded pictures and agent verbs."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from trestle.common import clock, codes
from trestle.common.outcome import classify
from trestle.common.types import (
    CatalogView,
    CleanupView,
    Handle,
    JoinMode,
    PluginSnapshot,
    PublishView,
    RequestOutcome,
    RunSpec,
    RunView,
)
from trestle.query.fs import FilesystemQueryBackend
from trestle.server.ledger import (
    TERMINAL_KINDS,
    RunLedger,
    evidence_dir,
    ledger_path,
)
from trestle.server.pins import PinStore
from trestle.server.projection import (
    build_summary,
    count_events,
    fetch_bytes,
    load_index,
    write_summary_json,
)
from trestle.server.registry import Registry
from trestle.server.runs import RunRegistry
from trestle.server.snapshots import load_declared

DEFAULT_SUMMARY_BUDGET = 4096


@dataclass
class Project:
    home: Path
    registry: Registry
    run_registry: RunRegistry
    # The restricted profile scopes `cancel` to the MCP session that started the run (WR-AUTH-1).
    session_scoped_cancel: bool = False
    # Status polls run on worker threads (L.CS-4.2), so two of them can overlap: a projection
    # rewrites the run's summary.json through one fixed temporary name, one writer at a time.
    _status_lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def status(self, run_id: Handle) -> RunView | RequestOutcome:
        with self._status_lock:
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

    async def await_one_async(self, run_id: Handle, wait_ms: int) -> RunView | RequestOutcome:
        deadline = time.monotonic() + (wait_ms / 1000.0)
        while True:
            view = await asyncio.to_thread(self.status, run_id)
            if isinstance(view, RequestOutcome):
                return view
            if view.state not in {"queued", "running"}:
                return view
            if time.monotonic() >= deadline:
                return view
            await asyncio.sleep(0.05)

    def await_terminal(self, run_id: Handle) -> RunView | RequestOutcome:
        """`completion="terminal"`: answer only from the finalized terminal row (the run view's
        state is terminal exactly when `evidence_finalized` and a terminal kind are both in the
        ledger), never a running frame. The wait is bounded by the run's admitted deadline plus
        `clock.finalization_margin`; past it the answer is `projection.terminal_wait_exceeded`."""
        limit = time.monotonic() + self._terminal_bound_s(run_id)
        while True:
            view = self.status(run_id)
            if isinstance(view, RequestOutcome) or view.state not in _NON_TERMINAL_STATES:
                return view
            if time.monotonic() >= limit:
                return _terminal_wait_exceeded(run_id)
            time.sleep(clock.poll_interval)

    async def await_terminal_async(self, run_id: Handle) -> RunView | RequestOutcome:
        limit = time.monotonic() + await asyncio.to_thread(self._terminal_bound_s, run_id)
        while True:
            view = await asyncio.to_thread(self.status, run_id)
            if isinstance(view, RequestOutcome) or view.state not in _NON_TERMINAL_STATES:
                return view
            if time.monotonic() >= limit:
                return _terminal_wait_exceeded(run_id)
            await asyncio.sleep(clock.poll_interval)

    def _terminal_bound_s(self, run_id: Handle) -> float:
        """Seconds from now to the moment the terminal wait gives up: the run's admitted
        `spec.deadline` (wall clock, fixed at admission) plus the finalization margin. A spec
        without a readable deadline falls back to its `timeout_s` from now, as the conductor
        does."""
        run_dir = self._run_dir_for(run_id)
        spec: dict[str, Any] = {}
        if run_dir is not None:
            try:
                loaded = json.loads((evidence_dir(run_dir) / "spec.json").read_text("utf-8"))
            except (OSError, ValueError):
                loaded = None
            if isinstance(loaded, dict):
                spec = loaded
        remaining: float | None = None
        raw = spec.get("deadline")
        if isinstance(raw, str):
            try:
                fixed = datetime.fromisoformat(raw)
            except ValueError:
                fixed = None
            if fixed is not None:
                if fixed.tzinfo is None:
                    fixed = fixed.replace(tzinfo=UTC)
                remaining = (fixed - datetime.now(tz=UTC)).total_seconds()
        if remaining is None:
            timeout = spec.get("timeout_s")
            remaining = float(timeout) if isinstance(timeout, (int, float)) else 300.0
        return max(remaining, 0.0) + clock.finalization_margin

    async def await_many_async(
        self,
        run_ids: list[Handle],
        mode: JoinMode,
        timeout_ms: int,
    ) -> list[RunView] | RequestOutcome:
        deadline = time.monotonic() + (timeout_ms / 1000.0)
        while True:
            views, outcome = await asyncio.to_thread(self._collect_run_views, run_ids)
            if outcome is not None:
                return outcome
            assert views is not None
            if _join_satisfied(views, mode):
                return views
            if time.monotonic() >= deadline:
                return views
            await asyncio.sleep(0.05)

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

    def cancel(self, run_id: Handle, caller_session: str | None = None) -> RequestOutcome:
        ledger = self._ledger_for(run_id)
        if ledger is None:
            return RequestOutcome(
                code=codes.INVALID_HANDLE,
                message=f"unknown run: {run_id}",
                retryable=False,
                origin="projection",
            )

        # Under the restricted profile an MCP session may cancel only a run it started: the
        # `created` row names the session that received it. This runs with the other checks, before
        # anything routes the cancel, and before the state check so a foreign run's state is not
        # revealed. A call outside an MCP session (the operator's CLI or console) is not scoped.
        if self.session_scoped_cancel and caller_session is not None:
            created = ledger.last_kind("created")
            if created is None or created.get("caller_session") != caller_session:
                return RequestOutcome(
                    code=codes.NOT_OWNER,
                    message=f"run not received in this session: {run_id}",
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

    def publish_plugin(
        self,
        source: str,
        *,
        name: str | None = None,
    ) -> PublishView | RequestOutcome:
        return self.registry.publish_source(source, name=name)

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

        # MC-12: a finalized run's count is on its `evidence_finalized` row, read with no file
        # scan; a run still in flight (or one finalized before the field existed) is counted live
        event_count = 0
        finalized = ledger.last_kind("evidence_finalized")
        recorded = finalized.get("event_count") if finalized is not None else None
        if isinstance(recorded, int) and not isinstance(recorded, bool):
            event_count = recorded
        elif evidence is not None:
            event_count = count_events(evidence)

        summary = None
        truncated = False
        omitted: list[str] | None = None
        next_handle: str | None = None
        result_bytes: int | None = None

        if state not in {"queued", "running"} and evidence is not None:
            index = load_index(evidence)
            result_path = evidence / "result.json"
            if index is not None and result_path.exists():
                spec = _read_spec(evidence)
                budget = self._summary_budget(spec)
                projection = build_summary(
                    run_id=run_id,
                    result_path=result_path,
                    index=index,
                    budget=budget,
                    declared_fields=self._declared_summary_fields(spec),
                )
                summary = projection.summary
                truncated = projection.truncated
                omitted = projection.omitted
                next_handle = projection.next_handle
                result_bytes = projection.result_bytes
                if projection.markers:
                    limits_exceeded = [
                        *(limits_exceeded if isinstance(limits_exceeded, list) else []),
                        *projection.markers,
                    ]
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
            cleanup=_cleanup_view(ledger, state),
            error=_error_view(ledger, state),
            outcome=_outcome_view(ledger, state, evidence),
        )

    def _summary_budget(self, spec: dict[str, object] | None) -> int:
        """The budget the run was admitted under: its own `spec.json`, whatever is published
        later (WR-TERM-5)."""
        budget = spec.get("summary_budget") if spec is not None else None
        if isinstance(budget, int) and not isinstance(budget, bool):
            return budget
        return DEFAULT_SUMMARY_BUDGET

    def _declared_summary_fields(self, spec: dict[str, object] | None) -> tuple[str, ...]:
        """The `summary_fields` the run's own snapshot declared (MC-18), read through
        `snapshots.load_declared`. A run whose snapshot is gone declares nothing."""
        if spec is None:
            return ()
        try:
            run_spec = RunSpec.from_dict(spec)
        except (KeyError, TypeError, ValueError):
            return ()
        plugin_path = self.home / "snapshots" / run_spec.snapshot_id / "plugin.py"
        if not plugin_path.is_file():
            return ()
        snap = PluginSnapshot(
            snapshot_id=run_spec.snapshot_id,
            plugin=run_spec.plugin,
            version=run_spec.version,
            source_path=str(plugin_path),
            source_sha256=run_spec.source_sha256,
            schema_sha256=run_spec.schema_sha256,
            manifest_sha256=run_spec.manifest_sha256,
            summary_budget=run_spec.summary_budget,
            timeout_s=run_spec.timeout_s,
        )
        return load_declared(snap).summary_fields


def _read_spec(evidence: Path) -> dict[str, object] | None:
    """The run's admitted spec, or None when it is missing or unreadable."""
    try:
        loaded = json.loads((evidence / "spec.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _terminal_wait_exceeded(run_id: Handle) -> RequestOutcome:
    return RequestOutcome(
        code=codes.TERMINAL_WAIT_EXCEEDED,
        message=(
            f"run {run_id} has no finalized terminal row within its deadline plus the "
            "finalization margin; query it by run id"
        ),
        retryable=False,
        origin="projection",
    )


def _error_view(ledger: RunLedger, state: str) -> dict[str, Any] | None:
    """The run's explanation, read from the ledger's `error_record` row (MC-15, the sole
    authority): {code, phase, message}. A run that has not ended carries none, so a row written
    just before the terminal row is not shown on a run still reported as running."""
    if state in _NON_TERMINAL_STATES:
        return None
    row = ledger.last_kind("error_record")
    if row is None:
        return None
    return {key: row.get(key) for key in ("code", "phase", "message")}


def _outcome_view(ledger: RunLedger, state: str, evidence: Path | None) -> dict[str, Any] | None:
    """The run's answer class (MC-17, B4-T4), beside `state`: classified from the terminal kind,
    the `error_record` row and whether recovery wrote the terminal row. Only `interrupted` is
    written by recovery. A run that has not ended has none. Identity is the snapshot the run's
    spec fixed at admission."""
    if state in _NON_TERMINAL_STATES:
        return None
    row = ledger.last_kind("error_record")
    outcome = classify(state, row, recovered=state == "interrupted")
    return outcome.to_dict(_spec_snapshot_id(evidence))


def _spec_snapshot_id(evidence: Path | None) -> str | None:
    if evidence is None:
        return None
    try:
        spec = json.loads((evidence / "spec.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    snapshot_id = spec.get("snapshot_id") if isinstance(spec, dict) else None
    return snapshot_id if isinstance(snapshot_id, str) else None


def _cleanup_view(ledger: RunLedger, state: str) -> CleanupView | None:
    """The cleanup disposition of a finished run's process-group target (B2-C9, B4-C7, MC-32).

    A run that ended without ever starting (finalized while queued, B2-C12) spawned nothing: it has
    no group target, and its cleanup reads `nothing_created`, the one path on which it does. For
    every run that did start, the target is `released` only when the run's `group_stop` row says
    the supervisor confirmed every attributable process gone, else `unknown`; no row is `unknown`,
    never clean. It is never `nothing_created`: the run spawned a process. (A core run records no
    lane, so no folded `InRunGroup` entry can hold `helpers_disclosed` back.)"""
    if state in _NON_TERMINAL_STATES:
        return None
    if not ledger.has_kind("started"):
        return CleanupView(processes="nothing_created")
    row = ledger.last_kind("group_stop")
    released = row is not None and row.get("confirmed_gone") is True
    return CleanupView(processes="released" if released else "unknown")


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
