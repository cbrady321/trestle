"""Project port — bounded pictures and agent verbs."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from trestle.common import clock, codes
from trestle.common.outcome import classify
from trestle.common.plan.compiler import REFUSAL_TEXT_MAX
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
from trestle.server import answer as answer_mod
from trestle.server import fold, idempotency
from trestle.server.home import is_locked, marked_run_dir, marker_path, owner_lock_path, read_marker
from trestle.server.ledger import (
    NON_TERMINAL_STATES,
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
from trestle.server.recovery import find_run_dir
from trestle.server.registry import Registry
from trestle.server.runs import RunRegistry
from trestle.server.runstate import trusted_state
from trestle.server.snapshots import load_declared

DEFAULT_SUMMARY_BUDGET = 4096


@dataclass(frozen=True)
class _Handles:
    """The handles an `await_runs` call waits on, run ids first and then each key's newest run;
    `labels[i]` is the key handle i was reached through (None for a run id)."""

    handles: list[Handle]
    labels: list[str | None]


@dataclass(frozen=True)
class _Child:
    """A child handle resolved against its root (V-1.1)."""

    root_id: Handle
    ledger: RunLedger
    run_dir: Path
    spec: dict[str, object]
    path: tuple[str, ...]


@dataclass
class Project:
    home: Path
    registry: Registry
    run_registry: RunRegistry
    # The restricted profile scopes `cancel`, and the read of a child view (L.TR-2.4), to the MCP
    # session that started the run (WR-AUTH-1).
    session_scoped_cancel: bool = False
    # Status polls run on worker threads (L.CS-4.2), so two of them can overlap: a projection
    # rewrites the run's summary.json through one fixed temporary name, one writer at a time.
    _status_lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)
    # v0.3.1 rule 3: a waiter that finds its run's owner lock free wakes the local reaper (the only
    # code that takes over a dead owner's run) and keeps polling. Set by a serving kernel.
    on_owner_gone: Callable[[], None] | None = field(default=None, repr=False, compare=False)

    def _wake_if_owner_gone(self, run_ids: list[Handle]) -> None:
        """Probe the owner lock of each waited-on run that still has a live marker: a free lock
        means its owner died, so the reaper is woken. Called at most once a second per wait."""
        if self.on_owner_gone is None:
            return
        for run_id in run_ids:
            if not marker_path(self.home, run_id).exists():
                continue
            run_dir = marked_run_dir(self.home, run_id, read_marker(self.home, run_id))
            if run_dir is not None and not is_locked(owner_lock_path(run_dir)):
                self.on_owner_gone()
                return

    def status(self, run_id: Handle, caller_session: str | None = None) -> RunView | RequestOutcome:
        """The run view of `run_id`: a root run, or a child handle (L.TR-2.2). Under the restricted
        profile a child view is read only by the session that admitted its root (WR-AUTH-1,
        L.TR-2.4); a root view is unscoped, as it has been. A call outside an MCP session
        (`caller_session` None: the operator's CLI or console) is not scoped."""
        with self._status_lock:
            # v0.3.1 Problem C: a live run is answered from its small state.json; the ledger is read
            # only for a terminal view, an old run, or a non-terminal state.json whose owner lock
            # is free (the owner may have died before writing it)
            run_dir = self._run_dir_for(run_id)
            state = trusted_state(run_dir) if run_dir is not None else None
            if run_dir is not None and state is not None and state["state"] in NON_TERMINAL_STATES:
                return _live_view(run_id, run_dir, state)
            ledger = RunLedger.open(ledger_path(run_dir)) if run_dir is not None else None
            if ledger is None:
                child = self._child_view(run_id, caller_session)
                if child is not None:
                    return child
                return RequestOutcome(
                    code=codes.INVALID_HANDLE,
                    message=f"unknown run: {run_id}",
                    retryable=False,
                    origin="projection",
                )
            return self._run_view(ledger, run_id)

    def await_one(
        self, run_id: Handle, wait_ms: int, caller_session: str | None = None
    ) -> RunView | RequestOutcome:
        deadline = time.monotonic() + (wait_ms / 1000.0)
        while True:
            view = self.status(run_id, caller_session)
            if isinstance(view, RequestOutcome):
                return view
            if view.state not in NON_TERMINAL_STATES:
                return view
            if time.monotonic() >= deadline:
                return view
            time.sleep(0.05)

    async def await_one_async(
        self, run_id: Handle, wait_ms: int, caller_session: str | None = None
    ) -> RunView | RequestOutcome:
        deadline = time.monotonic() + (wait_ms / 1000.0)
        while True:
            view = await asyncio.to_thread(self.status, run_id, caller_session)
            if isinstance(view, RequestOutcome):
                return view
            if view.state not in NON_TERMINAL_STATES:
                return view
            if time.monotonic() >= deadline:
                return view
            await asyncio.sleep(0.05)

    def await_terminal(
        self, run_id: Handle, caller_session: str | None = None
    ) -> RunView | RequestOutcome:
        """`completion="terminal"`: answer only from the finalized terminal row (the run view's
        state is terminal exactly when `evidence_finalized` and a terminal kind are both in the
        ledger), never a running frame. The wait is bounded by the run's admitted deadline plus
        `clock.finalization_margin`; past it the answer is `projection.terminal_wait_exceeded`."""
        limit = time.monotonic() + self._terminal_bound_s(run_id)
        probe_at = time.monotonic() + OWNER_PROBE_S
        while True:
            view = self.status(run_id, caller_session)
            if isinstance(view, RequestOutcome) or view.state not in NON_TERMINAL_STATES:
                return view
            if time.monotonic() >= limit:
                return _terminal_wait_exceeded(run_id)
            if time.monotonic() >= probe_at:
                self._wake_if_owner_gone([run_id])
                probe_at = time.monotonic() + OWNER_PROBE_S
            time.sleep(clock.await_poll_interval)

    async def await_terminal_async(
        self, run_id: Handle, caller_session: str | None = None
    ) -> RunView | RequestOutcome:
        limit = time.monotonic() + await asyncio.to_thread(self._terminal_bound_s, run_id)
        probe_at = time.monotonic() + OWNER_PROBE_S
        while True:
            view = await asyncio.to_thread(self.status, run_id, caller_session)
            if isinstance(view, RequestOutcome) or view.state not in NON_TERMINAL_STATES:
                return view
            if time.monotonic() >= limit:
                return _terminal_wait_exceeded(run_id)
            if time.monotonic() >= probe_at:
                await asyncio.to_thread(self._wake_if_owner_gone, [run_id])
                probe_at = time.monotonic() + OWNER_PROBE_S
            await asyncio.sleep(clock.await_poll_interval)

    def _terminal_bound_s(self, run_id: Handle) -> float:
        """Seconds from now to the moment the terminal wait gives up: the run's admitted
        `spec.deadline` (wall clock, fixed at admission) plus the finalization margin; for a run
        that was held, the deadline its `released` row minted once it has one (before that its spec
        deadline is `hold_until + deadline_s`, the latest possible). A spec without a readable
        deadline falls back to its `timeout_s` from now, as the conductor does."""
        run_dir = self._run_dir_for(run_id)
        spec: dict[str, Any] = {}
        minted: str | None = None
        if run_dir is not None:
            minted = RunLedger.open(ledger_path(run_dir)).released_deadline()
            try:
                loaded = json.loads((evidence_dir(run_dir) / "spec.json").read_text("utf-8"))
            except (OSError, ValueError):
                loaded = None
            if isinstance(loaded, dict):
                spec = loaded
        remaining: float | None = None
        raw = minted if minted is not None else spec.get("deadline")
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

    def resolve_handles(
        self, run_ids: list[Handle] | None, keys: list[str] | None
    ) -> _Handles | RequestOutcome:
        """What `await_runs` waits on (Feature 1): its run ids, then each key resolved to its
        newest run before the wait. Neither given is `projection.invalid_args`; a key no run used
        refuses the whole call `projection.unknown_key`, naming it, as an unknown run id does."""
        if not run_ids and not keys:
            return RequestOutcome(
                code=codes.PROJECTION_INVALID_ARGS,
                message="await_runs needs run_ids or keys",
                retryable=False,
                origin="projection",
            )
        handles = list(run_ids or [])
        labels: list[str | None] = [None] * len(handles)
        for key in keys or []:
            run_id = self._newest_run_of_key(key)
            if run_id is None:
                return RequestOutcome(
                    code=codes.PROJECTION_UNKNOWN_KEY,
                    message=f"unknown idempotency key: {key}"[:REFUSAL_TEXT_MAX],
                    retryable=False,
                    origin="projection",
                )
            handles.append(run_id)
            labels.append(key)
        return _Handles(handles, labels)

    def _newest_run_of_key(self, key: str) -> Handle | None:
        """The newest run that used `key` whose directory exists, expired or not (an expired key
        stays history until GC removes its run); None for a key no run used."""
        for entry in idempotency.read_entries(self.home, key):
            if find_run_dir(self.home, entry.run_id) is not None:
                return entry.run_id
        return None

    async def await_many_async(
        self,
        run_ids: list[Handle],
        mode: JoinMode,
        timeout_ms: int,
        caller_session: str | None = None,
        key_labels: list[str | None] | None = None,
    ) -> list[RunView] | RequestOutcome:
        deadline = time.monotonic() + (timeout_ms / 1000.0)
        probe_at = time.monotonic() + OWNER_PROBE_S
        while True:
            views, outcome = await asyncio.to_thread(
                self._collect_run_views, run_ids, caller_session, key_labels
            )
            if outcome is not None:
                return outcome
            assert views is not None
            if _join_satisfied(views, mode):
                return views
            if time.monotonic() >= deadline:
                return views
            if time.monotonic() >= probe_at:
                await asyncio.to_thread(self._wake_if_owner_gone, run_ids)
                probe_at = time.monotonic() + OWNER_PROBE_S
            await asyncio.sleep(clock.await_poll_interval)

    def await_many(
        self,
        run_ids: list[Handle],
        mode: JoinMode,
        timeout_ms: int,
        caller_session: str | None = None,
        key_labels: list[str | None] | None = None,
    ) -> list[RunView] | RequestOutcome:
        deadline = time.monotonic() + (timeout_ms / 1000.0)
        probe_at = time.monotonic() + OWNER_PROBE_S
        while True:
            views, outcome = self._collect_run_views(run_ids, caller_session, key_labels)
            if outcome is not None:
                return outcome
            assert views is not None
            if _join_satisfied(views, mode):
                return views
            if time.monotonic() >= deadline:
                return views
            if time.monotonic() >= probe_at:
                self._wake_if_owner_gone(run_ids)
                probe_at = time.monotonic() + OWNER_PROBE_S
            time.sleep(clock.await_poll_interval)

    def _collect_run_views(
        self,
        run_ids: list[Handle],
        caller_session: str | None = None,
        key_labels: list[str | None] | None = None,
    ) -> tuple[list[RunView] | None, RequestOutcome | None]:
        views: list[RunView] = []
        for index, run_id in enumerate(run_ids):
            view = self.status(run_id, caller_session)
            if isinstance(view, RequestOutcome):
                return None, view
            if key_labels is not None:
                view.idempotency_key = key_labels[index]
            views.append(view)
        return views, None

    def cancel(self, run_id: Handle, caller_session: str | None = None) -> RequestOutcome:
        ledger = self._ledger_for(run_id)
        if ledger is None:
            refused = self._cancel_child(run_id, caller_session)
            if refused is not None:
                return refused
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
        if state not in NON_TERMINAL_STATES:  # a held run is cancellable too (Feature 3)
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

    def _cancel_child(self, handle: Handle, caller_session: str | None) -> RequestOutcome | None:
        """A cancel addressed to a child handle (B2-C12, OQ-27 assumed default): after today's
        checks (an unknown handle stays `projection.invalid_handle`, a foreign session
        `projection.not_owner`, a root that cannot be cancelled `projection.invalid_handle`), the
        refusal `projection.cancel_not_root` naming the root. Nothing is written: no cancel flag,
        no ledger row, and the root runs on. None when `handle` is not a child handle."""
        child = self._resolve_child(handle, caller_session)
        if child is None or isinstance(child, RequestOutcome):
            return child
        if child.ledger.projected_state() not in NON_TERMINAL_STATES:
            return RequestOutcome(
                code=codes.INVALID_HANDLE,
                message=f"run not cancellable: {child.root_id} ({child.ledger.projected_state()})",
                retryable=False,
                origin="projection",
            )
        return RequestOutcome(
            code=codes.CANCEL_NOT_ROOT,
            message=f"cancel is addressed to the root run: {child.root_id}"[:REFUSAL_TEXT_MAX],
            retryable=False,
            origin="projection",
        )

    def query(
        self,
        view: str,
        params: dict[str, object],
        cursor: Handle | None = None,
    ) -> dict[str, object] | RequestOutcome:
        return FilesystemQueryBackend(self.home, run_view=self.status).query(view, params, cursor)

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

    def _resolve_child(
        self, handle: Handle, caller_session: str | None
    ) -> _Child | RequestOutcome | None:
        """`handle` as a child of an admitted root (V-1.1), or None when it is not one: a root that
        does not exist, one whose plan has no such vertex, or a handle that does not derive from
        (root run id, path) all read as unknown. Under the restricted profile only the session that
        received the root resolves its children (WR-AUTH-1, L.TR-2.4), as only it may cancel it;
        that is checked before the handle is resolved, so another session learns nothing of the
        root's vertices."""
        root_id = answer_mod.root_run_id_of(handle)
        if root_id is None:
            return None
        ledger = self._ledger_for(root_id)
        run_dir = self._run_dir_for(root_id)
        if ledger is None or run_dir is None:
            return None
        if self.session_scoped_cancel and caller_session is not None:
            created = ledger.last_kind("created")
            if created is None or created.get("caller_session") != caller_session:
                return RequestOutcome(
                    code=codes.NOT_OWNER,
                    message=f"run not received in this session: {root_id}",
                    retryable=False,
                    origin="projection",
                )
        spec = _read_spec(evidence_dir(run_dir)) or {}
        try:
            plan = fold.plan_of_spec(spec)
        except ValueError:
            return None
        path = answer_mod.child_paths(root_id, plan).get(handle)
        if path is None:
            return None
        return _Child(root_id, ledger, run_dir, spec, path)

    def _child_view(
        self, handle: Handle, caller_session: str | None = None
    ) -> RunView | RequestOutcome | None:
        """The view of a child handle (V-1.1, V-1.3; MC-B3-08), or None when `handle` is not one.
        `state` is the root's run state, so a child is non-terminal while its root is live; once the
        root is finalized the vertex's B4-C8 account is the materialized one (finalization and
        recovery wrote it), or, for a run finalized before it existed, recomputed from the same
        durable inputs."""
        child = self._resolve_child(handle, caller_session)
        if child is None or isinstance(child, RequestOutcome):
            return child
        root_id, ledger, run_dir, spec, path = (
            child.root_id,
            child.ledger,
            child.run_dir,
            child.spec,
            child.path,
        )
        state = ledger.projected_state()
        view = RunView(run_id=handle, state=state, root_run_id=root_id, path="/".join(path))
        if state in NON_TERMINAL_STATES:
            return view
        key = "/".join(path)
        wire = (answer_mod.read_child_views(run_dir) or {}).get(key)
        if wire is None:
            account = answer_mod.child_accounts(run_dir, spec).get(path)
            wire = answer_mod.node_wire(account) if account is not None else None
        view.answer = wire
        listing = wire.get("listing") if wire is not None else None
        view.disposition = listing if listing in _DISPOSITIONS else None
        return view

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

        if state not in NON_TERMINAL_STATES and evidence is not None:
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
            answer=_answer_view(ledger, state, run_dir),
            retry_of=_retry_of(ledger),
            after_run_id=_after_run_id(ledger.last_kind("created")),
            **_deadline_fields(ledger.last_kind("created")),
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


def _live_view(run_id: Handle, run_dir: Path, state: dict[str, Any]) -> RunView:
    """The view of a run that has not ended, from its state.json (v0.3.1 Problem C): the same
    fields `_run_view` reads from the ledger for such a run, which has no summary, cleanup, error,
    outcome or answer yet."""
    evidence = evidence_dir(run_dir)
    meta: dict[str, object] = {}
    try:
        loaded = json.loads((evidence / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        loaded = None
    if isinstance(loaded, dict):
        meta = loaded
    duration_ms = state.get("duration_ms")
    if not isinstance(duration_ms, int):
        meta_duration = meta.get("duration_ms")
        duration_ms = meta_duration if isinstance(meta_duration, int) else None
    limits_exceeded = meta.get("limits_exceeded")
    if limits_exceeded is None:
        limits_exceeded = state.get("limits_exceeded")
    raw_artifact_count = meta.get("artifact_count", 0)
    artifact_count = raw_artifact_count if isinstance(raw_artifact_count, int) else 0
    if artifact_count == 0:
        recorded = state.get("artifact_count")
        artifact_count = recorded if isinstance(recorded, int) else 0
    return RunView(
        run_id=run_id,
        state=str(state["state"]),
        duration_ms=duration_ms,
        event_count=count_events(evidence),
        artifact_count=artifact_count,
        limits_exceeded=limits_exceeded if isinstance(limits_exceeded, list) else None,
        retry_of=state.get("retry_of") if isinstance(state.get("retry_of"), str) else None,
        after_run_id=_after_run_id(state),
        **_deadline_fields(state),
    )


def _deadline_fields(record: dict[str, Any] | None) -> dict[str, Any]:
    """The run view's `deadline_s` and `deadline_source` (Feature 0), from the `created` row (or
    state.json); a run admitted before v0.3.1 has no recorded source and shows neither."""
    if record is None:
        return {}
    source = record.get("deadline_source")
    seconds = record.get("deadline_s")
    if not isinstance(source, str) or not isinstance(seconds, int | float):
        return {}
    return {"deadline_s": seconds, "deadline_source": source}


def _after_run_id(record: dict[str, Any] | None) -> str | None:
    """The run a held (or once held) run was sent to wait for, from `created` or state.json."""
    after = record.get("after_run_id") if record is not None else None
    return after if isinstance(after, str) else None


def _retry_of(ledger: RunLedger) -> str | None:
    created = ledger.last_kind("created")
    retry_of = created.get("retry_of") if created is not None else None
    return retry_of if isinstance(retry_of, str) else None


def _read_spec(evidence: Path) -> dict[str, object] | None:
    """The run's admitted spec, or None when it is missing or unreadable."""
    try:
        loaded = json.loads((evidence / "spec.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return loaded if isinstance(loaded, dict) else None


def _answer_view(ledger: RunLedger, state: str, run_dir: Path | None) -> dict[str, Any] | None:
    """B4-C6's one additive field: the `TerminalAnswer`, recomputed from the durable inputs U2
    wrote (B4-C1 Post), only once the terminal row exists (WR-TERM-2). Bounded by the run's own
    summary budget (CL-B1); the rest is behind its `detail` handle."""
    if state in NON_TERMINAL_STATES or run_dir is None:
        return None
    spec = _read_spec(evidence_dir(run_dir)) or {}
    answer = answer_mod.answer_for_run(run_dir, ledger.records, state, spec)
    return answer_mod.to_wire(answer, answer_mod.summary_budget_of(spec))


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
    if state in NON_TERMINAL_STATES:
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
    if state in NON_TERMINAL_STATES:
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
    if state in NON_TERMINAL_STATES:
        return None
    if not ledger.has_kind("started"):
        return CleanupView(processes="nothing_created")
    row = ledger.last_kind("group_stop")
    released = row is not None and row.get("confirmed_gone") is True
    # B2-C9, B2-C11: a release sweep that left a target unknown, or recovery declining to sweep a
    # plan of an unknown format, makes the cleanup unknown whatever the group stop says; the
    # answer (L.SV-4.2) composes the rest from the same rows
    if ledger.has_kind("sweep_skipped") or any(
        r.get("kind") == "sweep_disposition" and r.get("disposition") == "unknown"
        for r in ledger.records
    ):
        released = False
    return CleanupView(processes="released" if released else "unknown")


# how often a waiter probes its runs' owner locks (v0.3.1 rule 3's wake-up)
OWNER_PROBE_S = 1.0
_FAILURE_TERMINAL_STATES = TERMINAL_KINDS - frozenset({"succeeded"})


# B4 `Listing` values a child view reports as its `disposition` (MC-B3-08)
_DISPOSITIONS = frozenset({"not_started", "stopped", "unended"})


def _is_non_terminal(state: str) -> bool:
    return state in NON_TERMINAL_STATES


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
