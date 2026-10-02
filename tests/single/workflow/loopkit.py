"""Rig for the loop tests (L.SV-5.7 onward): a real `ChildRunServices` and a real attempt lane
under a manual clock, scripted work units, and an in-memory marker port.

The clock only moves when the loop waits (`RigCancel.wait`, the CONVERGE poll) or the test moves
it, so every wait bound is exercised without sleeping. The lane is the child's real one (fsync,
capacity, refusals), read back through the proof oracle `tests.proof.records.lane_rows`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from tests.proof import records
from trestle.child import run_services as rs
from trestle.common.plan import carving, compiler, formats
from trestle.workflow import loop, ports
from trestle.workflow import services as svc
from trestle.workflow.declarations import (
    CompletionSource,
    Compose,
    EffectDeclaration,
    EffectFacetClass,
    LeafDeclaration,
    Lifetime,
    LoopFlags,
    RealizationKind,
    RemedyDeclaration,
    Repeat,
    WaitPolicy,
    WorkflowEntry,
)
from trestle.workflow.extract import extract_root
from trestle.workflow.services import AttemptTicket
from trestle.workflow.units import (
    ActContext,
    Acted,
    EffectFacets,
    NoAction,
    ObserveContext,
    ReadFacets,
    Step,
)
from trestle.workflow.values import (
    CheckResult,
    ClockReading,
    Confirmation,
    ConfirmationStatus,
    CreatedHandle,
    Lineage,
    NodePath,
    Observation,
    OwnedHandle,
    SelectorRef,
    StopCause,
    Verdict,
)

NOW = datetime(2026, 9, 30, 12, 0, 0, tzinfo=UTC)
DEADLINE_S = 300.0
RESERVE_S = 10.0
MARGIN_S = 20.0
RELEASE_SLICE_S = 10.0
SPEC = ports.ResourceSpec("marker", RealizationKind.AGENT_LAUNCHED_PROJECT, "marker-entry", None)
EFFECT = "up"
STOP_EFFECT = "stop"
RUN_EFFECT = "run"


# ------------------------------------------------------------------------------ time and signals


class ManualClock:
    def __init__(self) -> None:
        self.now = NOW

    def advance(self, seconds: float) -> None:
        self.now = self.now + timedelta(seconds=seconds)


class RigCancel:
    """A `CancelSignal` whose `wait` advances the manual clock instead of sleeping, and returns
    True at once when a stop flag is up."""

    def __init__(self, clock: ManualClock) -> None:
        self._clock = clock
        self.stop: StopCause | None = None
        self.stop_on_wait: int | None = None  # raise the stop flag during the n-th wait (1-based)
        self.stop_cause = StopCause.CANCEL
        self.waits: list[timedelta] = []

    @property
    def requested(self) -> bool:
        return self.stop is not None

    def cause(self) -> StopCause | None:
        return self.stop

    def wait(self, timeout: timedelta) -> bool:
        self.waits.append(timeout)
        if self.stop_on_wait is not None and len(self.waits) >= self.stop_on_wait:
            self.stop = self.stop_cause
        if self.stop is not None:
            return True
        self._clock.advance(timeout.total_seconds())
        return False


class Sink:
    """An `EvidenceSink` that keeps every event."""

    def __init__(self) -> None:
        self.events: list[tuple[str, Mapping[str, Any]]] = []

    def event(self, kind: str, fields: Mapping[str, Any]) -> None:
        self.events.append((kind, dict(fields)))

    def kinds(self) -> list[str]:
        return [kind for kind, _ in self.events]


class RigServices:
    """B2's `RunServices` over a real `ChildRunServices` (identity, plan, lane), with the
    manual clock, the rig's cancel signal and its evidence sink. `plan` replaces the admitted plan
    the loop is shown (a tampered one for the digest proofs)."""

    def __init__(
        self,
        inner: rs.ChildRunServices,
        clock: ManualClock,
        cancel: RigCancel,
        sink: Sink,
        *,
        deadline: datetime,
        release_slice_s: float,
        plan: compiler.AdmittedPlan | None = None,
        slice_end: datetime | None = None,
    ) -> None:
        self._slice_end = slice_end
        self._inner = inner
        self._clock = clock
        self._cancel = cancel
        self._sink = sink
        self._deadline = deadline
        self._release_slice = release_slice_s
        self._plan = plan

    finalization_reserve_s = RESERVE_S
    currency_margin_s = MARGIN_S

    def admitted(self) -> svc.AdmittedPlan:
        real = self._inner.admitted()
        if self._plan is None:
            return real
        return svc.AdmittedPlan(real.lineage_root, self._plan)

    def lineage(self, path: NodePath) -> Lineage:
        return self._inner.lineage(path)

    def child_run_id(self, path: NodePath) -> str:
        return self._inner.child_run_id(path)

    def clock(self) -> ClockReading:
        return ClockReading(
            now=self._clock.now,
            root_deadline=self._deadline,
            release_point=self._deadline - timedelta(seconds=self._release_slice),
        )

    def slice_end(self, path: NodePath) -> datetime:
        if self._slice_end is not None:
            return self._slice_end
        return self._inner.slice_end(path)

    def cancellation(self) -> RigCancel:
        return self._cancel

    def release_point_reached(self) -> None:
        """The child's flag file, in the rig: the release-point stop is up from here."""
        self._cancel.stop = self._cancel.stop or StopCause.RELEASE_POINT

    def attempts(self) -> svc.AttemptLane:
        return self._inner.attempts()

    def evidence(self) -> Sink:
        return self._sink


# ------------------------------------------------------------------------------ declarations


def effect(
    name: str,
    facet: EffectFacetClass,
    *,
    lifetime: Lifetime = Lifetime.RUN,
    release: bool = False,
    release_timeout_s: float | None = 2.0,
) -> EffectDeclaration:
    return EffectDeclaration(
        effect=name,
        facet=facet,
        verb=name,
        lifetime=lifetime,
        host_sections=frozenset(),
        release_timeout=None if release_timeout_s is None else timedelta(seconds=release_timeout_s),
        is_release=release,
    )


def declaration(
    *,
    completion: CompletionSource = CompletionSource.OBSERVED,
    repeat: Repeat = Repeat.SAFE,
    effects: Sequence[EffectDeclaration] = (),
    preconditions: tuple[str, ...] = (),
    retryable: frozenset[str] = frozenset(),
    remedies: tuple[RemedyDeclaration, ...] = (),
    max_attempts: int = 3,
    poll_s: float = 1.0,
    max_wait_s: float = 10.0,
    budget_s: float = 60.0,
) -> LeafDeclaration:
    return LeafDeclaration(
        unit="unit",
        flags=LoopFlags(Compose.LEAF, completion, repeat),
        preconditions=preconditions,
        postcondition="ready",
        wait=WaitPolicy(timedelta(seconds=poll_s), 1.0, timedelta(seconds=max_wait_s)),
        resource_kind="marker",
        may_touch=frozenset({"marker"}),
        effects=tuple(effects),
        retryable=retryable,
        remedies=remedies,
        budget=timedelta(seconds=budget_s),
        max_attempts=max_attempts,
    )


MARKER_EFFECTS = (
    effect(EFFECT, EffectFacetClass.CREATE),
    effect(STOP_EFFECT, EffectFacetClass.OWNED, release=True),
)


# ------------------------------------------------------------------------------ units


ObserveFn = Callable[["Unit", Any, ReadFacets, ObserveContext], Observation]
AdvanceFn = Callable[["Unit", Any, Verdict, EffectFacets, ActContext], Step]


def observation(
    *,
    present: bool | None = None,
    selector_present: bool = False,
    found: int = 0,
    ready: bool = False,
    pre: tuple[bool, ...] = (),
    code: str | None = None,
    detail: str = "",
    preconditions: tuple[str, ...] | None = None,
    currency: tuple[Any, ...] = (),
) -> Observation:
    """A well-formed observation whose preconditions are named `pre0`.. unless told otherwise."""
    from trestle.workflow.values import FoundRef

    refs = tuple(FoundRef("marker", f"f{i}", NOW) for i in range(found))
    names = (
        preconditions if preconditions is not None else tuple(f"pre{i}" for i in range(len(pre)))
    )
    return Observation(
        present=(selector_present or bool(refs)) if present is None else present,
        selector_present=selector_present,
        identity_proven=True,
        configuration_compatible=True,
        postcondition=CheckResult(ready, None, detail),
        preconditions=tuple(
            (n, CheckResult(ok, None, "")) for n, ok in zip(names, pre, strict=False)
        ),
        currency=currency,
        found=refs,
        code=code,
        payload=None,
    )


class Unit:
    """A work unit scripted by two functions; every call is logged."""

    def __init__(
        self,
        decl: LeafDeclaration,
        observe: ObserveFn | None = None,
        advance: AdvanceFn | None = None,
        release: Callable[..., Step] | None = None,
    ) -> None:
        self.decl = decl
        self._observe = observe
        self._advance = advance
        self._release = release
        self.observes = 0
        self.advances = 0
        self.releases = 0
        self.log: list[str] = []

    def declare(self) -> LeafDeclaration:
        return self.decl

    def observe(self, params: Any, reads: ReadFacets, ctx: ObserveContext) -> Observation:
        self.observes += 1
        self.log.append("observe")
        assert self._observe is not None
        return self._observe(self, params, reads, ctx)

    def advance(self, params: Any, state: Verdict, effects: EffectFacets, ctx: ActContext) -> Step:
        self.advances += 1
        self.log.append("advance")
        if self._advance is None:
            return Acted()
        return self._advance(self, params, state, effects, ctx)

    def release(self, params: Any, handle: CreatedHandle, effects: Any, ctx: ActContext) -> Step:
        self.releases += 1
        self.log.append("release")
        if self._release is None:
            return NoAction("unit.no_release")
        return self._release(self, params, handle, effects, ctx)


# ------------------------------------------------------------------------------ ports


class Marker:
    """An in-memory marker behind `ResourceReads`, `ResourceCreate` and `ResourceOwned`: each
    `create` makes one instance (keyed by its effect id), ready after `ready_after` observations;
    an instance is gone `absent_after` observations after its `stop` (never, if `stop_holds`);
    `restart` answers `restart_status` (a remedy's repair, L.SL-6.1) and changes nothing."""

    def __init__(
        self,
        *,
        ready_after: int = 0,
        absent_after: int = 0,
        stop_holds: bool = False,
        create_status: ConfirmationStatus = ConfirmationStatus.APPLIED,
        create_code: str | None = None,
        vanish_after_create: bool = False,
        descriptor: ports.ReleaseDescriptor | None = None,
        restart_status: ConfirmationStatus = ConfirmationStatus.APPLIED,
        restart_code: str | None = None,
    ) -> None:
        self.vanish_after_create = vanish_after_create
        self.restart_status = restart_status
        self.restart_code = restart_code
        self.restarts = 0
        self.descriptor = descriptor or ports.InRunGroup()
        self.ready_after = ready_after
        self.absent_after = absent_after
        self.stop_holds = stop_holds
        self.create_status = create_status
        self.create_code = create_code
        self.live: set[str] = set()
        self.stopped_at: dict[str, int] = {}  # effect -> observation count at its stop
        self.observations = 0
        self.calls: list[str] = []
        self.stops: list[str] = []  # the effect of each handle `stop` was called with

    @property
    def present(self) -> bool:
        return bool(self.live)

    # ResourceReads
    def observe(
        self, spec: ports.ResourceSpec, lineage: Lineage, effect: str | None
    ) -> ports.ResourceObservation:
        self.calls.append("observe")
        self.observations += 1
        if not self.stop_holds:
            for name, at in list(self.stopped_at.items()):
                if self.observations - at > self.absent_after:
                    self.live.discard(name)
                    del self.stopped_at[name]
        ref = (
            SelectorRef(lineage, effect or EFFECT, f"sel-{lineage.root_run_id}", NOW)
            if self.present
            else None
        )
        return ports.ResourceObservation(
            selector_present=self.present,
            selector_ref=ref,
            identity_proven=True,
            configuration_compatible=True,
            currency=(),
            found=(),
            code=None,
        )

    def check(self, check: str, target: Any) -> CheckResult:
        self.calls.append("check")
        return CheckResult(self.present and self.observations > self.ready_after, None, "")

    def endpoint(self, target: Any, vantage: Any) -> Any:
        raise NotImplementedError

    # ResourceCreate
    def release_descriptor(self, call: ports.EffectCall) -> ports.ReleaseDescriptor:
        return self.descriptor

    def launch_policy(self, spec: ports.ResourceSpec) -> ports.ExecutionPolicy:
        raise NotImplementedError

    def create(self, spec: ports.ResourceSpec, ticket: AttemptTicket) -> Confirmation:
        self.calls.append("create")
        if self.create_status is ConfirmationStatus.APPLIED:
            if not self.vanish_after_create:
                self.live.add(ticket.effect)
            return Confirmation(
                ConfirmationStatus.APPLIED,
                None,
                f"sel-{ticket.lineage.root_run_id}-{ticket.effect}",
            )
        return Confirmation(self.create_status, self.create_code, None)

    # ResourceOwned
    def stop(self, target: CreatedHandle, ticket: AttemptTicket) -> Confirmation:
        self.calls.append("stop")
        self.stops.append(target.effect)
        self.stopped_at[target.effect] = self.observations
        return Confirmation(ConfirmationStatus.APPLIED, None, None)

    def restart(self, target: OwnedHandle, ticket: AttemptTicket) -> Confirmation:
        self.calls.append("restart")
        self.restarts += 1
        return Confirmation(self.restart_status, self.restart_code, None)

    def recreate(self, target: OwnedHandle, ticket: AttemptTicket) -> Confirmation:
        raise NotImplementedError


class RunPort(ports.EventFacet, Protocol):
    def run(
        self, name: str, ticket: AttemptTicket
    ) -> tuple[Confirmation, ports.HasRecordedResult | None]: ...


class Runner:
    """An event port whose confirmations are scripted, one per call (the last repeats)."""

    def __init__(self, *outcomes: Confirmation) -> None:
        self.outcomes = list(outcomes)
        self.calls = 0

    def release_descriptor(self, call: ports.EffectCall) -> ports.ReleaseDescriptor:
        return ports.InRunGroup()

    def run(
        self, name: str, ticket: AttemptTicket
    ) -> tuple[Confirmation, ports.HasRecordedResult | None]:
        outcome = self.outcomes[min(self.calls, len(self.outcomes) - 1)]
        self.calls += 1
        return outcome, None


def marker_ports(marker: Marker) -> dict[type, object]:
    return {ports.ResourceReads: marker, ports.ResourceCreate: marker, ports.ResourceOwned: marker}


def marker_unit(
    marker: Marker,
    decl: LeafDeclaration | None = None,
    *,
    advance: AdvanceFn | None = None,
) -> Unit:
    """observe reads the marker through `ResourceReads`; advance creates it through the facet."""

    def observe(unit: Unit, params: Any, reads: ReadFacets, ctx: ObserveContext) -> Observation:
        resource = reads.read(ports.ResourceReads)
        seen = resource.observe(SPEC, ctx.lineage, EFFECT)
        ready = (
            resource.check("ready", seen.selector_ref) if seen.selector_ref is not None else None
        )
        return observation(
            selector_present=seen.selector_present,
            ready=ready is not None and ready.satisfied,
        )

    def default_advance(
        unit: Unit, params: Any, state: Verdict, effects: EffectFacets, ctx: ActContext
    ) -> Step:
        effects.create(ports.ResourceCreate).create(SPEC, EFFECT)
        return Acted()

    def release(
        unit: Unit, params: Any, handle: CreatedHandle, effects: Any, ctx: ActContext
    ) -> Step:
        effects.owned(ports.ResourceOwned).stop(handle, STOP_EFFECT)
        return Acted()

    return Unit(
        decl or declaration(effects=MARKER_EFFECTS),
        observe,
        advance or default_advance,
        release,
    )


# ------------------------------------------------------------------------------ the rig


def redigest(plan: compiler.AdmittedPlan, **changes: Any) -> compiler.AdmittedPlan:
    """`plan` with `changes` and a digest that covers them."""
    changed = replace(plan, **changes)
    return replace(changed, plan_digest=formats.plan_digest(changed.body()))


def admit(entry: WorkflowEntry, deadline_s: float = DEADLINE_S) -> compiler.AdmittedPlan:
    """The plan admission would write for `entry`: compiled, carved, digest attached."""
    _, tree = extract_root(entry)
    compiled = compiler.compile(tree, {})
    assert isinstance(compiled, compiler.AdmittedPlan), compiled
    release_slice = carving.release_slice_for(compiled, RELEASE_SLICE_S)
    slices = carving.carve(compiled, deadline_s, RESERVE_S, release_slice)
    assert isinstance(slices, dict), slices
    return carving.attach(compiled, slices, release_slice)


@dataclass
class Rig:
    run_dir: Path
    entry: WorkflowEntry
    unit: Unit
    plan: compiler.AdmittedPlan
    services: RigServices
    clock: ManualClock
    cancel: RigCancel
    sink: Sink
    ports: dict[type, object] = field(default_factory=dict)
    intent: dict[str, Any] = field(default_factory=dict)
    sleeps: list[float] = field(default_factory=list)

    def sleep(self, seconds: float) -> None:
        """The RELEASE wait: a plain clock wait that only moves the manual clock."""
        self.sleeps.append(seconds)
        self.clock.advance(seconds)

    def loop(self) -> loop.Loop:
        return loop.Loop(self.services, self.entry, self.intent, self.ports, sleep=self.sleep)

    def run(self) -> None:
        self.loop().run()

    def lane(self) -> records.LaneRows:
        return records.lane_rows(self.run_dir)

    def classes(self) -> list[str]:
        return [row.cls for row in self.lane().rows]

    def rows(self, cls: str) -> list[dict[str, Any]]:
        return [row.entry for row in self.lane().rows if row.cls == cls]

    def ends(self) -> list[dict[str, Any]]:
        return self.rows("end")


def build(
    tmp_path: Path,
    unit: Unit,
    *,
    ports: Mapping[type, object] | None = None,
    deadline_s: float = DEADLINE_S,
    lane_entries: int | None = None,
    plan: Callable[[compiler.AdmittedPlan], compiler.AdmittedPlan] | None = None,
    shown: Callable[[compiler.AdmittedPlan], compiler.AdmittedPlan] | None = None,
    entry: WorkflowEntry | None = None,
    intent: Mapping[str, Any] | None = None,
    slice_end_s: float | None = None,
) -> Rig:
    """A rig around `unit`. `plan` transforms the admitted (written) plan, `shown` only the plan
    the loop is shown (a tampered copy), `entry` overrides the entry the loop walks."""
    admitted_entry = WorkflowEntry(
        root="unit", units={"unit": unit}, deadline=timedelta(seconds=deadline_s)
    )
    admitted = admit(admitted_entry, deadline_s)
    if lane_entries is not None:
        admitted = redigest(admitted, lane_entries=lane_entries)
    if plan is not None:
        admitted = plan(admitted)
    run_dir = tmp_path / "r_loop_0001"
    (run_dir / "work").mkdir(parents=True, exist_ok=True)
    (run_dir / "evidence").mkdir(parents=True, exist_ok=True)
    clock = ManualClock()
    cancel = RigCancel(clock)
    sink = Sink()
    deadline = NOW + timedelta(seconds=deadline_s)
    inner = rs.build_run_services(
        rs.ServicesInput(
            run_dir=run_dir,
            plan=admitted,
            deadline=deadline,
            event=lambda kind, **fields: None,
            event_max=64 * 1024,
            now=lambda: clock.now,
            reserve_s=RESERVE_S,
            margin_s=MARGIN_S,
        )
    )
    shown_plan = shown(admitted) if shown is not None else None
    services = RigServices(
        inner,
        clock,
        cancel,
        sink,
        deadline=deadline,
        release_slice_s=admitted.release_slice,
        plan=shown_plan,
        slice_end=None if slice_end_s is None else NOW + timedelta(seconds=slice_end_s),
    )
    return Rig(
        run_dir=run_dir,
        entry=entry or admitted_entry,
        unit=unit,
        plan=admitted,
        services=services,
        clock=clock,
        cancel=cancel,
        sink=sink,
        ports=dict(ports or {}),
        intent=dict(intent or {}),
    )


# ------------------------------------------------------------------------------ in memory


class MemoryLane:
    """An `AttemptLane` that keeps nothing durable and logs `record_plan` and `record_end`; any
    call that would start an effect fails the test."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def record_plan(self, plan: svc.PlanIdentity) -> None:
        self.calls.append(("record_plan", plan))

    def issue(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("an effect was started")

    def confirm(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("an effect was started")

    def record_result(self, *args: Any, **kwargs: Any) -> None:
        raise AssertionError("an effect was started")

    def record_step(self, step: Any) -> None:
        self.calls.append(("record_step", step))

    def record_released(self, *args: Any, **kwargs: Any) -> None:
        raise AssertionError("an effect was started")

    def record_end(self, end: svc.NodeEnd) -> None:
        self.calls.append(("record_end", end))

    def node_record(self, path: NodePath) -> Any:
        from trestle.workflow.units import NodeRecordView

        return NodeRecordView()

    def committed_length(self) -> int:
        return len(self.calls)


class MemoryServices:
    """`RunServices` over an in-memory plan and lane: no run directory, no file."""

    def __init__(self, plan: compiler.AdmittedPlan, lane: MemoryLane | None = None) -> None:
        self.plan = plan
        self.lane = lane or MemoryLane()
        self.clock_ = ManualClock()
        self.cancel = RigCancel(self.clock_)
        self.sink = Sink()

    def admitted(self) -> svc.AdmittedPlan:
        return svc.AdmittedPlan("r_mem_0001", self.plan)

    def lineage(self, path: NodePath) -> Lineage:
        return Lineage("r_mem_0001", path)

    def child_run_id(self, path: NodePath) -> str:
        return "r_mem_0001"

    def clock(self) -> ClockReading:
        deadline = NOW + timedelta(seconds=DEADLINE_S)
        return ClockReading(self.clock_.now, deadline, deadline)

    def slice_end(self, path: NodePath) -> datetime:
        return NOW + timedelta(seconds=DEADLINE_S)

    def cancellation(self) -> RigCancel:
        return self.cancel

    def release_point_reached(self) -> None:
        self.cancel.stop = self.cancel.stop or StopCause.RELEASE_POINT

    def attempts(self) -> MemoryLane:
        return self.lane

    def evidence(self) -> Sink:
        return self.sink
