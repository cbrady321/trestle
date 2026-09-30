"""The child's RunServices (L.SV-5.2; MC-B2-02, B2-C3..C7, B2-C13, B2-C14).

For a workflow run the child builds B2's `RunServices` from the run's admitted plan
(`spec.plan`) and binds it on the `Context` as `RunContext.run_services`; a plain plugin never
sees it. The services keep no state of their own: identity, deadline and slices are pure over the
admitted plan and the admitted deadline (one clock, minted at admission), the cancel signal
reflects two flag files, the attempt lane is the durable record.

The Protocols and the value types they name live in `trestle.workflow.services` (the workflow
package imports no child, server or lane module, C.5 step 4). This module is the one place the
two vocabularies meet, and it converts field by field at each call: a `NodePath` is a lane path
tuple, an enum is its value, a release descriptor is its V-10 form or the wire mapping the loop
carries, and the lane's `NodeRecord` becomes `units.NodeRecordView` (the only record shape the
loop reads). The SA-01 drift node (`tests/proof/drift/single/test_sa01_lane.py`) ties the view
classes to the lane's by field name and wire-level type.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from trestle.child.attempt_lane import AttemptLane as LaneWriter
from trestle.child.attempt_lane import AttemptTicket as LaneTicket
from trestle.common import clock as clock_limits
from trestle.common import lane_format as lf
from trestle.common.plan import carving
from trestle.workflow import services as svc
from trestle.workflow import units, values
from trestle.workflow.declarations import EffectFacetClass, JsonValue, Lifetime, Repeat

CANCEL_FLAG = "cancel.flag"
RELEASE_POINT_FLAG = "release_point.flag"


# ------------------------------------------------------------------------------- identity


def child_run_id(root_run_id: str, path: values.NodePath) -> str:
    """The `ChildRunId` of `path` under `root_run_id` (V-1.1): derived from the root's run id and
    the canonical path, so it is fixed at admission and opaque to every caller. Executor-chosen
    form: `<root run id>.<12 hex of sha256(root, path)>`, which no run id can equal (a run id
    holds no '.')."""
    digest = hashlib.sha256(
        f"{root_run_id}\0{lf.encode_path(tuple(path.segments))}".encode()
    ).hexdigest()
    return f"{root_run_id}.{digest[:12]}"


# ------------------------------------------------------------------------------- cancel signal


class FlagCancelSignal:
    """V-2 `CancelSignal` over the two flag files (B2-C6): `requested` once either exists,
    `cause()` naming CANCEL before RELEASE_POINT, `wait` returning promptly. It reflects the flag
    files only; the answer's class never comes from `cause()` (B2-C15)."""

    def __init__(self, work: Path, *, poll: float | None = None) -> None:
        self._cancel = work / CANCEL_FLAG
        self._release_point = work / RELEASE_POINT_FLAG
        # the supervisor's published poll interval (clock.poll_interval): the same latency the
        # host itself promises for seeing a flag
        self._poll = clock_limits.poll_interval if poll is None else poll

    @property
    def requested(self) -> bool:
        return self.cause() is not None

    def cause(self) -> values.StopCause | None:
        if self._cancel.exists():
            return values.StopCause.CANCEL
        if self._release_point.exists():
            return values.StopCause.RELEASE_POINT
        return None

    def wait(self, timeout: timedelta) -> bool:
        """Block until a flag exists or `timeout` elapses; True iff one exists on return."""
        end = time.monotonic() + max(timeout.total_seconds(), 0.0)
        while True:
            if self.requested:
                return True
            remaining = end - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(self._poll, remaining))


# ------------------------------------------------------------------------------- evidence sink


def _event_bytes(kind: str, payload: Mapping[str, Any]) -> int:
    """The size the context measures an event at (`RuntimeContext._emit`)."""
    return len(
        json.dumps({"kind": kind, "payload": payload}, separators=(",", ":")).encode("utf-8")
    )


class ContextEvidenceSink:
    """V-13 `EvidenceSink` over `Context.event` (B2-C13): an event over `EVENT_MAX` is truncated
    and marked, and no failure ever reaches the loop or a port. The context's own limits, its
    scrubber and its reserved kinds apply unchanged (`Context.event`); a refused kind is dropped.

    Truncated form (executor-chosen): the payload becomes `{"truncated": true, "original_bytes":
    N, "excerpt": <the payload's JSON text cut to fit>}`. A field named `kind` is renamed
    `field_kind` (`Context.event` takes `kind` itself)."""

    def __init__(self, emit: Callable[..., None], event_max: int) -> None:
        self._emit = emit
        self._max = event_max

    def event(self, kind: str, fields: Mapping[str, JsonValue]) -> None:
        try:
            payload: dict[str, Any] = json.loads(json.dumps(dict(fields), default=str))
            if "kind" in payload:
                payload["field_kind"] = payload.pop("kind")
            size = _event_bytes(kind, payload)
            if size > self._max:
                payload = self._truncated(kind, payload, size)
            self._emit(kind, **payload)
        except Exception:  # noqa: BLE001 (B2-C13: it never raises into the loop or a port)
            return

    def _truncated(self, kind: str, payload: dict[str, Any], size: int) -> dict[str, Any]:
        text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)

        def marked(n: int) -> dict[str, Any]:
            return {"truncated": True, "original_bytes": size, "excerpt": text[:n]}

        low, high = 0, len(text)
        while low < high:  # the longest excerpt that still fits
            mid = (low + high + 1) // 2
            if _event_bytes(kind, marked(mid)) <= self._max:
                low = mid
            else:
                high = mid - 1
        return marked(low)


# ------------------------------------------------------------------------------- conversions


def _lane_path(path: values.NodePath) -> tuple[str, ...]:
    return tuple(path.segments)


def _wf_lineage(lineage: lf.Lineage) -> values.Lineage:
    return values.Lineage(lineage.root_run_id, values.NodePath(tuple(lineage.path)))


def _lane_lineage(lineage: values.Lineage) -> lf.Lineage:
    return lf.Lineage(lineage.root_run_id, _lane_path(lineage.path))


def _wire(release: lf.ReleaseDescriptor) -> dict[str, Any]:
    return lf.descriptor_record(release)


def descriptor_to_lane(release: object) -> lf.ReleaseDescriptor:
    """A V-10 descriptor in any of the forms the loop carries: the lane's own, the wire mapping
    (`{"form": "in_run_group" | "durable" | "argv", ...}` as the lane writes it), or an object of
    the workflow ports' classes `InRunGroup` / `Durable` / `ArgvRelease`, recognised by name and
    field names. Anything else raises `TypeError` (nothing is written)."""
    if isinstance(release, (lf.InRunGroup, lf.ArgvRelease, lf.Durable)):
        return release
    if isinstance(release, Mapping):
        form = release.get("form")
        if form == "in_run_group":
            return lf.InRunGroup(helpers_disclosed=bool(release["helpers_disclosed"]))
        if form == "durable":
            return lf.Durable(owner=lf.DurableOwner(release["owner"]))
        if form == "argv":
            remove = release.get("remove_argv")
            return lf.ArgvRelease(
                executable=release["executable"],
                observe_argv=tuple(release["observe_argv"]),
                observe_ok_exit=frozenset(release["observe_ok_exit"]),
                stop_argv=tuple(release["stop_argv"]),
                timeout=timedelta(seconds=release["timeout_s"]),
                remove_argv=None if remove is None else tuple(remove),
            )
        raise TypeError(f"unknown release form {form!r}")
    name = type(release).__name__
    if name == "InRunGroup":
        return lf.InRunGroup(helpers_disclosed=bool(getattr(release, "helpers_disclosed", False)))
    if name == "Durable":
        owner = release.owner  # type: ignore[attr-defined]
        return lf.Durable(owner=lf.DurableOwner(getattr(owner, "value", owner)))
    if name == "ArgvRelease":
        remove = getattr(release, "remove_argv", None)
        return lf.ArgvRelease(
            executable=release.executable,  # type: ignore[attr-defined]
            observe_argv=tuple(release.observe_argv),  # type: ignore[attr-defined]
            observe_ok_exit=frozenset(release.observe_ok_exit),  # type: ignore[attr-defined]
            stop_argv=tuple(release.stop_argv),  # type: ignore[attr-defined]
            timeout=release.timeout,  # type: ignore[attr-defined]
            remove_argv=None if remove is None else tuple(remove),
        )
    raise TypeError(f"not a release descriptor: {name}")


def _wf_remedy(remedy: lf.RemedyGrant | None) -> values.RemedyGrant | None:
    return (
        None if remedy is None else values.RemedyGrant(remedy.code, remedy.effect, remedy.attempt)
    )


def _lane_remedy(remedy: values.RemedyGrant | None) -> lf.RemedyGrant | None:
    return None if remedy is None else lf.RemedyGrant(remedy.code, remedy.effect, remedy.attempt)


def _wf_confirmation(conf: lf.Confirmation | None) -> values.Confirmation | None:
    if conf is None:
        return None
    return values.Confirmation(
        values.ConfirmationStatus(conf.status.value), conf.code, conf.identity
    )


def _lane_confirmation(conf: values.Confirmation) -> lf.Confirmation:
    return lf.Confirmation(lf.ConfirmationStatus(conf.status.value), conf.code, conf.identity)


def _wf_result(result: lf.RecordedResult | None) -> values.RecordedResult | None:
    if result is None:
        return None
    counts = result.counts
    return values.RecordedResult(
        result.passed,
        result.code,
        None
        if counts is None
        else values.TestCounts(counts.passed, counts.failed, counts.errors, counts.skipped),
    )


def _lane_result(result: values.RecordedResult) -> lf.RecordedResult:
    counts = result.counts
    return lf.RecordedResult(
        result.passed,
        result.code,
        None
        if counts is None
        else lf.TestCounts(counts.passed, counts.failed, counts.errors, counts.skipped),
    )


def _wf_handle(handle: lf.CreatedHandle | None) -> values.CreatedHandle | None:
    if handle is None:
        return None
    return values.CreatedHandle(
        _wf_lineage(handle.lineage), handle.effect, handle.selector, _wire(handle.release)
    )


def _lane_handle(handle: values.CreatedHandle | None) -> lf.CreatedHandle | None:
    if handle is None:
        return None
    return lf.CreatedHandle(
        _lane_lineage(handle.lineage),
        handle.effect,
        handle.selector,
        descriptor_to_lane(handle.release),
    )


def _wf_ticket(ticket: LaneTicket) -> svc.AttemptTicket:
    return svc.AttemptTicket(
        lineage=_wf_lineage(ticket.lineage),
        effect=ticket.effect,
        facet=EffectFacetClass(ticket.facet.value),
        attempt=ticket.attempt,
        repeat=Repeat(ticket.repeat.value),
        lifetime=Lifetime(ticket.lifetime.value),
        release=_wire(ticket.release),
        remedy=_wf_remedy(ticket.remedy),
    )


def _lane_ticket(ticket: svc.AttemptTicket) -> LaneTicket:
    return LaneTicket(
        lineage=_lane_lineage(ticket.lineage),
        effect=ticket.effect,
        facet=lf.EffectFacetClass(ticket.facet.value),
        attempt=ticket.attempt,
        repeat=lf.Repeat(ticket.repeat.value),
        lifetime=lf.Lifetime(ticket.lifetime.value),
        release=descriptor_to_lane(ticket.release),
        remedy=_lane_remedy(ticket.remedy),
    )


def ticket_view(entry: lf.TicketEntry) -> units.TicketView:
    """One lane `TicketEntry` as the loop-side `TicketView`, field by field (A1c2-11)."""
    return units.TicketView(
        lineage=_wf_lineage(entry.lineage),
        effect=entry.effect,
        facet=EffectFacetClass(entry.facet.value),
        attempt=entry.attempt,
        repeat=Repeat(entry.repeat.value),
        lifetime=Lifetime(entry.lifetime.value),
        release=_wire(entry.release),
        remedy=_wf_remedy(entry.remedy),
        issued_at=entry.issued_at,
        confirmation=_wf_confirmation(entry.confirmation),
        handle=_wf_handle(entry.handle),
        result=_wf_result(entry.result),
        released_at=entry.released_at,
        release_outcome=entry.release_outcome,
    )


def step_view(entry: lf.StepEntry) -> units.StepView:
    """One lane `StepEntry` as the loop-side `StepView`, field by field."""
    return units.StepView(
        lineage=_wf_lineage(entry.lineage),
        at=entry.at,
        kind=values.StepKind(entry.kind.value),
        code=entry.code,
        human_action=entry.human_action,
        resend=None if entry.resend is None else values.Resend(entry.resend.value),
        handle=_wf_handle(entry.handle),
    )


def node_record_view(record: lf.NodeRecord) -> units.NodeRecordView:
    """V-4's durable `NodeRecord` as the `NodeRecordView` the loop and the join read (MC-B2-02).
    Only the durable record; the loop adds its held steps (`with_held`, L.SV-5.7)."""
    return units.NodeRecordView(
        tickets=tuple(ticket_view(t) for t in record.tickets),
        steps=tuple(step_view(s) for s in record.steps),
    )


def _lane_step(step: units.StepView) -> lf.StepEntry:
    return lf.StepEntry(
        lineage=_lane_lineage(step.lineage),
        at=step.at,
        kind=lf.StepKind(step.kind.value),
        code=step.code,
        human_action=step.human_action,
        resend=None if step.resend is None else lf.Resend(step.resend.value),
        handle=_lane_handle(step.handle),
    )


def _lane_end(end: svc.NodeEnd) -> lf.NodeEnd:
    return lf.NodeEnd(
        lineage=_lane_lineage(end.lineage),
        at=end.at,
        condition=None if end.condition is None else lf.Condition(end.condition.value),
        code=end.code,
        human_action=end.human_action,
        resend=None if end.resend is None else lf.Resend(end.resend.value),
        provenance=None if end.provenance is None else lf.Provenance(end.provenance.value),
        cut=None if end.cut is None else lf.Cut(end.cut.value),
    )


def _lane_plan(plan: svc.PlanIdentity) -> lf.PlanIdentity:
    return lf.PlanIdentity(
        declaration_digest=plan.declaration_digest,
        args_hash=plan.args_hash,
        selection={_lane_path(k): _lane_path(v) for k, v in plan.selection},
        observations_digest=plan.observations_digest,
    )


def _refusal(refusal: lf.LaneRefusal | None) -> svc.LaneRefusal | None:
    return None if refusal is None else svc.LaneRefusal(refusal.value)


# ------------------------------------------------------------------------------- attempt lane


class ChildAttemptLane:
    """B2's `AttemptLane` in the workflow package's types, over the lane writer (L.SV-1.2). Every
    rule, order, durability and refusal is the writer's; this class only converts."""

    def __init__(self, lane: LaneWriter) -> None:
        self._lane = lane

    def record_plan(self, plan: svc.PlanIdentity) -> None:
        self._lane.record_plan(_lane_plan(plan))

    def issue(
        self,
        lineage: values.Lineage,
        effect: str,
        facet: EffectFacetClass,
        repeat: Repeat,
        lifetime: Lifetime,
        release: object,
        max_attempts: int,
        remedy: values.RemedyGrant | None,
    ) -> svc.AttemptTicket | values.TicketRefusal:
        issued = self._lane.issue(
            _lane_lineage(lineage),
            effect,
            lf.EffectFacetClass(facet.value),
            lf.Repeat(repeat.value),
            lf.Lifetime(lifetime.value),
            descriptor_to_lane(release),
            max_attempts,
            _lane_remedy(remedy),
        )
        if isinstance(issued, LaneTicket):
            return _wf_ticket(issued)
        return values.TicketRefusal(issued.value)

    def confirm(
        self, ticket: svc.AttemptTicket, confirmation: values.Confirmation
    ) -> values.CreatedHandle | None:
        return _wf_handle(
            self._lane.confirm(_lane_ticket(ticket), _lane_confirmation(confirmation))
        )

    def record_result(self, ticket: svc.AttemptTicket, result: values.RecordedResult) -> None:
        self._lane.record_result(_lane_ticket(ticket), _lane_result(result))

    def record_step(self, step: units.StepView) -> None | svc.LaneRefusal:
        return _refusal(self._lane.record_step(_lane_step(step)))

    def record_released(self, handle: values.CreatedHandle, outcome: str | None) -> None:
        lane_handle = _lane_handle(handle)
        assert lane_handle is not None
        self._lane.record_released(lane_handle, outcome)

    def record_end(self, end: svc.NodeEnd) -> None | svc.LaneRefusal:
        return _refusal(self._lane.record_end(_lane_end(end)))

    def node_record(self, path: values.NodePath) -> units.NodeRecordView:
        return node_record_view(self._lane.node_record(_lane_path(path)))

    def committed_length(self) -> int:
        return self._lane.committed_length()


# ------------------------------------------------------------------------------- run services


@dataclass(frozen=True)
class ServicesInput:
    """What the child hands `build_run_services`."""

    run_dir: Path
    plan: svc.PlanAccepted
    deadline: datetime  # the admitted deadline, minted at admission (B2-C5)
    event: Callable[..., None]  # `Context.event`
    event_max: int  # V-13 EVENT_MAX (`CaptureLimits.max_single_event_bytes`)
    now: Callable[[], datetime] = lambda: datetime.now(UTC)  # noqa: E731
    reserve_s: float = clock_limits.FINALIZATION_RESERVE_S  # the carve's reserve (B2-C5)
    margin_s: float = clock_limits.finalization_margin  # J-25's currency margin (V-3.1)


class ChildRunServices:
    """`RunServices` for one admitted root run (B2-C3..C6, B2-C13); no `sections()` at one
    vertex (B2-C8 carried)."""

    def __init__(self, given: ServicesInput) -> None:
        self._given = given
        self._plan = given.plan
        self._root = given.run_dir.name  # the root run id is the run directory's (B2-I3)
        self._scope = given.plan.selected_scope
        self._release_point = given.deadline - timedelta(seconds=given.plan.release_slice)
        self._ends: dict[str, float] | None = None
        self._lane: ChildAttemptLane | None = None
        self._lane_lock = threading.Lock()
        self._cancel = FlagCancelSignal(given.run_dir / "work")
        self._sink = ContextEvidenceSink(given.event, given.event_max)

    @property
    def finalization_reserve_s(self) -> float:
        """`svc.FinalizationBounds`: the reserve the carve holds back per parent."""
        return self._given.reserve_s

    @property
    def currency_margin_s(self) -> float:
        """`svc.FinalizationBounds`: the finalization margin (V-3.1 J-25)."""
        return self._given.margin_s

    def admitted(self) -> svc.AdmittedPlan:
        return svc.AdmittedPlan(lineage_root=self._root, accepted=self._plan)

    def _known(self, path: values.NodePath) -> tuple[str, ...]:
        segments = tuple(path.segments)
        if segments not in self._scope:
            raise svc.UnknownNode("/".join(segments) or "<root>")
        return segments

    def lineage(self, path: values.NodePath) -> values.Lineage:
        return values.Lineage(self._root, values.NodePath(self._known(path)))

    def child_run_id(self, path: values.NodePath) -> str:
        return child_run_id(self._root, values.NodePath(self._known(path)))

    def clock(self) -> values.ClockReading:
        return values.ClockReading(
            now=self._given.now(),
            root_deadline=self._given.deadline,
            release_point=self._release_point,
        )

    def slice_end(self, path: values.NodePath) -> datetime:
        segments = self._known(path)
        if not segments:
            return self._release_point  # the root has no carve (V-8 L-3, B2-C5)
        if self._ends is None:
            self._ends = carving.slice_ends(self._plan, self._given.reserve_s)
        offset = self._ends.get("/".join(segments))
        if offset is None:
            raise svc.UnknownNode("/".join(segments))
        return self._given.deadline + timedelta(seconds=offset)

    def cancellation(self) -> values.CancelSignal:
        return self._cancel

    def attempts(self) -> svc.AttemptLane:
        with self._lane_lock:
            if self._lane is None:
                self._lane = ChildAttemptLane(
                    LaneWriter(
                        self._given.run_dir,
                        self._plan.lane_entries,
                        self._scope,
                        now=self._given.now,  # one clock: entries are stamped as `clock()` reads
                    )
                )
            return cast(svc.AttemptLane, self._lane)

    def evidence(self) -> values.EvidenceSink:
        return self._sink


def build_run_services(given: ServicesInput) -> ChildRunServices:
    return ChildRunServices(given)
