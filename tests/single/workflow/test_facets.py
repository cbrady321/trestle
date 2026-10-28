"""L.SV-5.6: port Protocols, V-10 descriptor forms and the ticketed facets (B1-C6, B1-E4, B1-E6,
V-5.4, V-5.5, V-10). Spy lanes and spy ports pin the order of B1-C6's steps; one real attempt lane
(through the child's RunServices adapter) proves the claim is durable before the port is called."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import pytest

from tests.single.workflow.test_run_services import make_services
from trestle.child import run_services as rs
from trestle.common import lane_format as lf
from trestle.workflow import codes, human_actions, ports
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
    Repeat,
    WaitPolicy,
)
from trestle.workflow.facets import (
    EffectBinder,
    FacetContext,
    PortContractViolation,
    PortNotBound,
    ReadBinder,
    ReleaseBinder,
)
from trestle.workflow.units import EffectRefused, NodeRecordView, StepView, TicketView
from trestle.workflow.values import (
    CancelSignal,
    Confirmation,
    ConfirmationStatus,
    CreatedHandle,
    Goal,
    Lineage,
    NodePath,
    RecordedResult,
    RemedyGrant,
    Resend,
    StepKind,
    StopCause,
    TicketRefusal,
)

NOW = datetime(2026, 9, 30, 11, 55, 0, tzinfo=UTC)
LINEAGE = Lineage("r_svc_0001", NodePath(("a",)))
ROOT_RELEASE = timedelta(seconds=30)
SPEC = ports.ResourceSpec("db", RealizationKind.DOCKER_SERVICE, "db-entry", None)
GROUP = ports.InRunGroup(helpers_disclosed=False)
APPLIED = Confirmation(ConfirmationStatus.APPLIED, None, "sel-1")
NOT_APPLIED = Confirmation(ConfirmationStatus.NOT_APPLIED, "test.refused", None)
UNKNOWN = Confirmation(ConfirmationStatus.UNKNOWN, None, None)


def effect(
    name: str,
    facet: EffectFacetClass,
    *,
    lifetime: Lifetime = Lifetime.RUN,
    release: bool = False,
    release_timeout: timedelta | None = ROOT_RELEASE,
) -> EffectDeclaration:
    return EffectDeclaration(
        effect=name,
        facet=facet,
        verb=name,
        lifetime=lifetime,
        host_sections=frozenset(),
        release_timeout=release_timeout,
        is_release=release,
    )


def declaration(*effects: EffectDeclaration, max_attempts: int = 3) -> LeafDeclaration:
    return LeafDeclaration(
        unit="leaf",
        flags=LoopFlags(Compose.LEAF, CompletionSource.OBSERVED, Repeat.SAFE),
        preconditions=(),
        postcondition="ready",
        wait=WaitPolicy(timedelta(seconds=1), 1.0, timedelta(seconds=10)),
        resource_kind="db",
        may_touch=frozenset(),
        effects=effects,
        retryable=frozenset(),
        remedies=(),
        budget=timedelta(seconds=20),
        max_attempts=max_attempts,
    )


DECL = declaration(
    effect("up", EffectFacetClass.CREATE),
    effect("restart", EffectFacetClass.OWNED),
    effect("stop", EffectFacetClass.OWNED, release=True),
    effect("test", EffectFacetClass.EVENT),
    effect("start", EffectFacetClass.SAFE_START, lifetime=Lifetime.DURABLE, release_timeout=None),
)

HANDLE = CreatedHandle(LINEAGE, "up", "sel-1", GROUP)


# ------------------------------------------------------------------------------ spies


@dataclass
class Flag:
    requested: bool = False

    def cause(self) -> StopCause | None:
        return StopCause.CANCEL if self.requested else None

    def wait(self, timeout: timedelta) -> bool:
        return self.requested


class SpyLane:
    """An AttemptLane that logs every call and returns scripted refusals."""

    def __init__(self, log: list[tuple[Any, ...]]) -> None:
        self.log = log
        self.refuse: TicketRefusal | None = None
        self.step_refusal: svc.LaneRefusal | None = None
        self.tickets: tuple[TicketView, ...] = ()
        self.issued: list[svc.AttemptTicket] = []
        self.steps: list[StepView] = []
        self.issue_args: list[tuple[Any, ...]] = []

    def record_plan(self, plan: svc.PlanIdentity) -> None:
        raise AssertionError("a facet never records the plan")

    def issue(
        self,
        lineage: Lineage,
        effect: str,
        facet: EffectFacetClass,
        repeat: Repeat,
        lifetime: Lifetime,
        release: object,
        max_attempts: int,
        remedy: RemedyGrant | None,
    ) -> svc.AttemptTicket | TicketRefusal:
        self.log.append(("issue", effect))
        self.issue_args.append(
            (lineage, effect, facet, repeat, lifetime, release, max_attempts, remedy)
        )
        if self.refuse is not None:
            return self.refuse
        ticket = svc.AttemptTicket(
            lineage, effect, facet, len(self.issued) + 1, repeat, lifetime, release, remedy
        )
        self.issued.append(ticket)
        return ticket

    def confirm(
        self, ticket: svc.AttemptTicket, confirmation: Confirmation
    ) -> CreatedHandle | None:
        self.log.append(("confirm", ticket.effect, confirmation))
        applied = confirmation.status is ConfirmationStatus.APPLIED
        if applied and ticket.facet is EffectFacetClass.CREATE and confirmation.identity:
            return CreatedHandle(
                ticket.lineage, ticket.effect, confirmation.identity, ticket.release
            )
        return None

    def record_result(self, ticket: svc.AttemptTicket, result: RecordedResult) -> None:
        self.log.append(("result", ticket.effect, result))

    def record_step(self, step: StepView) -> None | svc.LaneRefusal:
        self.log.append(("step", step))
        self.steps.append(step)
        return self.step_refusal

    def record_released(self, handle: CreatedHandle, outcome: str | None) -> None:
        raise AssertionError("a facet never records a release")

    def record_end(self, end: svc.NodeEnd) -> None | svc.LaneRefusal:
        raise AssertionError("a facet never ends a node")

    def node_record(self, path: NodePath) -> NodeRecordView:
        return NodeRecordView(tickets=self.tickets)

    def committed_length(self) -> int:
        return 0


class SpyCreate:
    def __init__(self, log: list[tuple[Any, ...]], result: Confirmation = APPLIED) -> None:
        self.log = log
        self.result = result
        self.raises: Exception | None = None
        self.calls: list[ports.EffectCall] = []

    def release_descriptor(self, call: ports.EffectCall) -> ports.ReleaseDescriptor:
        self.log.append(("descriptor", call.member))
        self.calls.append(call)
        return GROUP

    def launch_policy(self, spec: ports.ResourceSpec) -> ports.ExecutionPolicy:
        return ports.ExecutionPolicy(
            ports.SelfProvisioning.DISABLED_BY_CONFIGURATION, ports.Helpers.PREVENTED, None
        )

    def create(self, spec: ports.ResourceSpec, ticket: svc.AttemptTicket) -> Confirmation:
        self.log.append(("port", "create", ticket))
        if self.raises is not None:
            raise self.raises
        return self.result


class SpyOwned:
    def __init__(self, log: list[tuple[Any, ...]]) -> None:
        self.log = log

    def release_descriptor(self, call: ports.EffectCall) -> ports.ReleaseDescriptor:
        self.log.append(("descriptor", call.member))
        return GROUP

    def restart(self, target: object, ticket: svc.AttemptTicket) -> Confirmation:
        self.log.append(("port", "restart", ticket))
        return APPLIED

    def recreate(self, target: object, ticket: svc.AttemptTicket) -> Confirmation:
        self.log.append(("port", "recreate", ticket))
        return APPLIED

    def stop(self, target: object, ticket: svc.AttemptTicket) -> Confirmation:
        self.log.append(("port", "stop", ticket))
        return APPLIED


class SpyExec:
    def __init__(self, log: list[tuple[Any, ...]]) -> None:
        self.log = log
        self.result: tuple[Confirmation, ports.ExecutionResult | None] = (APPLIED, PASSED)

    def release_descriptor(self, call: ports.EffectCall) -> ports.ReleaseDescriptor:
        self.log.append(("descriptor", call.member))
        return GROUP

    def policy(self, command: ports.BoundCommand) -> ports.ExecutionPolicy:
        return ports.ExecutionPolicy(
            ports.SelfProvisioning.DISABLED_BY_CONFIGURATION, ports.Helpers.PREVENTED, None
        )

    def run(
        self,
        command: ports.BoundCommand,
        ticket: svc.AttemptTicket,
        cancel: CancelSignal,
        until: datetime,
    ) -> tuple[Confirmation, ports.ExecutionResult | None]:
        self.log.append(("port", "run", ticket))
        return self.result


PASSED = ports.ExecutionResult(
    0, ports.ExecutionClass.PASSED, None, (), None, "ok"
)  # counts are not needed by the facet
COMMAND = ports.BoundCommand(
    "test", ("/bin/true",), {}, ports.Resolved("/bin/true", "1", "p", "a"), False
)


@dataclass
class Rig:
    log: list[tuple[Any, ...]] = field(default_factory=list)
    goal: Goal = Goal.CONVERGE
    flips: list[int] = field(default_factory=list)
    held: list[StepView] = field(default_factory=list)
    flag: Flag = field(default_factory=Flag)
    remedy: RemedyGrant | None = None
    decl: LeafDeclaration = DECL

    def __post_init__(self) -> None:
        self.lane = SpyLane(self.log)
        self.create = SpyCreate(self.log)
        self.owned = SpyOwned(self.log)
        self.exec = SpyExec(self.log)

    def flip(self) -> None:
        self.log.append(("flip",))
        self.flips.append(1)
        self.goal = Goal.RELEASE

    def context(
        self,
        lane: Any = None,
        lineage: Lineage = LINEAGE,
        only_ports: dict[type, object] | None = None,
    ) -> FacetContext:
        return FacetContext(
            lane=lane if lane is not None else self.lane,
            lineage=lineage,
            declaration=self.decl,
            ports=only_ports
            if only_ports is not None
            else {
                ports.ResourceCreate: self.create,
                ports.ResourceOwned: self.owned,
                ports.ExecutionPort: self.exec,
            },
            cancellation=self.flag,
            goal=lambda: self.goal,
            flip_goal=self.flip,
            hold=self.held.append,
            now=lambda: NOW,
            remedy=self.remedy,
        )

    def effects(self) -> EffectBinder:
        return EffectBinder(self.context())

    def kinds(self) -> list[str]:
        return [entry[0] for entry in self.log]


def ticket_view(effect_id: str, status: ConfirmationStatus | None) -> TicketView:
    return TicketView(
        lineage=LINEAGE,
        effect=effect_id,
        facet=EffectFacetClass.CREATE,
        attempt=1,
        repeat=Repeat.SAFE,
        lifetime=Lifetime.RUN,
        release=GROUP,
        remedy=None,
        issued_at=NOW,
        confirmation=None if status is None else Confirmation(status, None, None),
        handle=None,
        result=None,
        released_at=None,
        release_outcome=None,
    )


# ------------------------------------------------------------------------------ B1-C6 order


@pytest.mark.proves(
    "WR-CANCEL-4", "WR-CANCEL-4:A-descriptor-before-effect", "A", "single", "PROC", "CI"
)
def test_descriptor_derived_before_ticket_and_port_call() -> None:
    rig = Rig()
    rig.effects().create(ports.ResourceCreate).create(SPEC, "up")
    assert rig.kinds() == ["descriptor", "issue", "port", "confirm"]
    (call,) = rig.create.calls
    assert call.member == "create"
    assert dict(call.arguments) == {"spec": SPEC}  # the ticket is excluded
    assert call.lineage == LINEAGE and call.effect == "up"
    assert call.lifetime is Lifetime.RUN and call.release_timeout == ROOT_RELEASE  # copied
    # the descriptor the port returned is what the ticket was issued with
    assert rig.lane.issue_args[0][5] == GROUP
    # a wire mapping returned by a port is validated into exactly one form before the issue
    rig2 = Rig()
    rig2.create.release_descriptor = lambda call: {"form": "durable", "owner": "host"}  # type: ignore[method-assign, assignment, return-value]
    rig2.effects().create(ports.ResourceCreate).create(SPEC, "up")
    assert rig2.lane.issue_args[0][5] == ports.Durable(ports.DurableOwner.HOST)
    # a mapping matching no form is refused before any ticket
    rig3 = Rig()
    rig3.create.release_descriptor = lambda call: {"form": "docker"}  # type: ignore[method-assign, assignment, return-value]
    with pytest.raises(ports.DescriptorError):
        rig3.effects().create(ports.ResourceCreate).create(SPEC, "up")
    assert rig3.kinds() == []


def test_ticket_durable_before_port_call(tmp_path: Path) -> None:
    services = make_services(tmp_path)
    lane = services.attempts()
    lane.record_plan(svc.PlanIdentity("d" * 64, "a" * 64, (), "o" * 64))
    seen: dict[str, Any] = {}

    class Probing(SpyCreate):
        def create(self, spec: ports.ResourceSpec, ticket: svc.AttemptTicket) -> Confirmation:
            # at the moment the port runs, the claim is already in the committed lane file
            view = lane.node_record(LINEAGE.path)
            seen["tickets"] = view.tickets
            seen["file"] = lf.committed_length(lf.lane_path(services._given.run_dir))
            return APPLIED

    rig = Rig()
    rig.create = Probing(rig.log)
    binder = EffectBinder(rig.context(lane=lane, only_ports={ports.ResourceCreate: rig.create}))
    before = lf.committed_length(lf.lane_path(services._given.run_dir))
    binder.create(ports.ResourceCreate).create(SPEC, "up")
    (ticket_at_call,) = seen["tickets"]
    assert ticket_at_call.effect == "up" and ticket_at_call.confirmation is None
    assert seen["file"] > before  # the issue entry was fsync'd into the lane before the call
    after = lane.node_record(LINEAGE.path).tickets[0]
    assert after.confirmation is not None and after.handle is not None
    assert after.release == lf.descriptor_record(lf.InRunGroup(False))  # recorded with the ticket


def test_confirm_after_port_created_handle_only_applied_create(tmp_path: Path) -> None:
    services = make_services(tmp_path)
    lane = services.attempts()
    lane.record_plan(svc.PlanIdentity("d" * 64, "a" * 64, (), "o" * 64))

    def bind(path: str, rig: Rig) -> EffectBinder:
        return EffectBinder(
            rig.context(lane=lane, lineage=Lineage("r_svc_0001", NodePath((path,))))
        )

    # APPLIED CREATE: a handle, carrying the recorded descriptor
    rig = Rig()
    bind("a", rig).create(ports.ResourceCreate).create(SPEC, "up")
    record = lane.node_record(NodePath(("a",)))
    (up,) = record.tickets
    assert isinstance(up.handle, CreatedHandle) and up.handle.selector == "sel-1"
    # NOT_APPLIED CREATE: a ticket, no handle
    rig = Rig()
    rig.create.result = NOT_APPLIED
    bind("b", rig).create(ports.ResourceCreate).create(SPEC, "up")
    (refused,) = lane.node_record(NodePath(("b",))).tickets
    assert refused.handle is None
    assert refused.confirmation is not None and refused.confirmation.code == "test.refused"
    # APPLIED EVENT and APPLIED OWNED: never a handle; the event's result is projected
    rig = Rig()
    rig.exec.result = (
        APPLIED,
        ports.ExecutionResult(1, ports.ExecutionClass.FAILED, None, ("t1",), "test.failed", "boom"),
    )
    node = bind("a", rig)
    node.owned(ports.ResourceOwned).restart(HANDLE, "restart")
    node.event(ports.ExecutionPort).run(COMMAND, "test", Flag(), NOW)
    tickets = lane.node_record(NodePath(("a",))).tickets
    restart, test = tickets[1], tickets[2]
    assert restart.handle is None and test.handle is None
    assert test.result == RecordedResult(False, "test.failed", None)  # R.recorded, nothing else


def test_event_result_contract(tmp_path: Path) -> None:
    rig = Rig()
    events = rig.effects().event(ports.ExecutionPort)
    events.run(COMMAND, "test", Flag(), NOW)
    assert rig.kinds() == ["descriptor", "issue", "port", "confirm", "result"]
    assert rig.log[-1][2] == RecordedResult(True, None, None)
    # None is permitted only with NOT_APPLIED
    rig = Rig()
    rig.exec.result = (NOT_APPLIED, None)
    rig.effects().event(ports.ExecutionPort).run(COMMAND, "test", Flag(), NOW)
    assert "result" not in rig.kinds()
    rig = Rig()
    rig.exec.result = (APPLIED, None)
    with pytest.raises(PortContractViolation):
        rig.effects().event(ports.ExecutionPort).run(COMMAND, "test", Flag(), NOW)
    assert rig.kinds()[-1] == "confirm"  # the attempt is recorded as resolved first


def test_stop_flag_after_ticket_not_applied_zero_port_calls(tmp_path: Path) -> None:
    rig = Rig()
    rig.flag.requested = True
    with pytest.raises(EffectRefused) as raised:
        rig.effects().create(ports.ResourceCreate).create(SPEC, "up")
    assert raised.value.refusal is TicketRefusal.RELEASE_POINT_PASSED
    assert raised.value.code == codes.TICKET_REFUSED
    assert "port" not in rig.kinds()
    # descriptor, ticket durable, then the stop check records NOT_APPLIED(STOP_SEEN); the loop
    # flips the goal before the refusal is raised
    assert rig.kinds() == ["descriptor", "issue", "confirm", "flip"]
    assert rig.log[2][2] == Confirmation(ConfirmationStatus.NOT_APPLIED, codes.STOP_SEEN, None)
    assert rig.goal is Goal.RELEASE and rig.held == [] and rig.lane.steps == []
    # the same on a real lane: the ticket is in the record, resolved not applied, no handle
    services = make_services(tmp_path)
    lane = services.attempts()
    lane.record_plan(svc.PlanIdentity("d" * 64, "a" * 64, (), "o" * 64))
    rig = Rig()
    rig.flag.requested = True
    binder = EffectBinder(rig.context(lane=lane, only_ports={ports.ResourceCreate: rig.create}))
    with pytest.raises(EffectRefused):
        binder.create(ports.ResourceCreate).create(SPEC, "up")
    (ticket,) = lane.node_record(LINEAGE.path).tickets
    assert ticket.confirmation == Confirmation(
        ConfirmationStatus.NOT_APPLIED, codes.STOP_SEEN, None
    )
    assert ticket.handle is None and "port" not in rig.kinds()


def test_goal_release_refuses_non_release() -> None:
    rig = Rig(goal=Goal.RELEASE)
    with pytest.raises(EffectRefused) as raised:
        rig.effects().create(ports.ResourceCreate).create(SPEC, "up")
    assert raised.value.refusal is TicketRefusal.RELEASE_POINT_PASSED
    # nothing is issued or recorded, and no flip is needed: the goal already is RELEASE
    assert "issue" not in rig.kinds() and "port" not in rig.kinds()
    assert rig.lane.steps == [] and rig.flips == [] and rig.held == []
    # a declared release effect goes through under RELEASE, with no stop check
    rig.flag.requested = True
    ReleaseBinder(rig.context(), HANDLE).owned(ports.ResourceOwned).stop(HANDLE, "stop")
    assert rig.kinds()[-3:] == ["issue", "port", "confirm"]
    assert rig.flips == []


def test_port_raise_records_unknown_first() -> None:
    rig = Rig()
    rig.create.raises = OSError("engine gone")
    with pytest.raises(OSError, match="engine gone"):
        rig.effects().create(ports.ResourceCreate).create(SPEC, "up")
    assert rig.kinds() == ["descriptor", "issue", "port", "confirm"]
    assert rig.log[-1][2] == Confirmation(ConfirmationStatus.UNKNOWN, None, None)
    assert rig.lane.steps == [] and rig.flips == []  # the loop's flip is B1-E6's, not the facet's


def test_issue_carries_facet_max_attempts_remedy() -> None:
    granted = RemedyGrant("tool.busy", "restart", 1)
    rig = Rig(remedy=granted)
    effects = rig.effects()
    effects.owned(ports.ResourceOwned).restart(HANDLE, "restart")
    effects.create(ports.ResourceCreate).create(SPEC, "up")
    restart_args, up_args = rig.lane.issue_args
    # (lineage, effect, facet, repeat, lifetime, release, max_attempts, remedy)
    assert restart_args[:5] == (
        LINEAGE,
        "restart",
        EffectFacetClass.OWNED,
        Repeat.SAFE,
        Lifetime.RUN,
    )
    assert restart_args[6] == 3 and restart_args[7] == granted  # the granted remedy's own effect
    assert up_args[2] is EffectFacetClass.CREATE and up_args[6] == 3
    assert up_args[7] is None  # any other effect carries no remedy
    rig = Rig(decl=declaration(*DECL.effects, max_attempts=7))
    rig.effects().create(ports.ResourceCreate).create(SPEC, "up")
    assert rig.lane.issue_args[0][6] == 7


class Starter(ports.SafeStartFacet, Protocol):
    def start(self, target: object, ticket: svc.AttemptTicket) -> Confirmation: ...


class SpyStart:
    def __init__(self, log: list[tuple[Any, ...]]) -> None:
        self.log = log
        self.calls: list[ports.EffectCall] = []

    def release_descriptor(self, call: ports.EffectCall) -> ports.ReleaseDescriptor:
        self.calls.append(call)
        return ports.Durable(ports.DurableOwner.HOST)

    def start(self, target: object, ticket: svc.AttemptTicket) -> Confirmation:
        self.log.append(("port", "start", ticket))
        return APPLIED


def test_safe_start_declared_durable_copies_lifetime_and_timeout_unchanged() -> None:
    rig = Rig()
    starter = SpyStart(rig.log)
    binder = EffectBinder(rig.context(only_ports={Starter: starter}))
    binder.safe_start(Starter).start("found-ref", "start")
    (call,) = starter.calls
    assert call.lifetime is Lifetime.DURABLE and call.release_timeout is None
    assert dict(call.arguments) == {"target": "found-ref"}
    args = rig.lane.issue_args[0]
    assert args[2] is EffectFacetClass.SAFE_START and args[4] is Lifetime.DURABLE


# ------------------------------------------------------------------------------ B1-E4's table


def refused(rig: Rig, effect_id: str = "up") -> EffectRefused:
    with pytest.raises(EffectRefused) as raised:
        rig.effects().create(ports.ResourceCreate).create(SPEC, effect_id)
    assert "port" not in rig.kinds()  # a refusal is always before any port call
    assert raised.value.code == codes.TICKET_REFUSED
    return raised.value


@pytest.mark.parametrize(
    "refusal", [TicketRefusal.ONCE_ALREADY_ISSUED, TicketRefusal.ATTEMPTS_SPENT]
)
def test_refusal_table_b1_e4_nothing_new(refusal: TicketRefusal) -> None:
    rig = Rig()
    rig.lane.refuse = refusal
    assert refused(rig).refusal is refusal
    assert rig.lane.steps == [] and rig.held == [] and rig.flips == []


def test_refusal_table_b1_e4() -> None:
    # LANE_UNAVAILABLE: the Blocked step goes to the hold callback only; the lane cannot take it
    rig = Rig()
    rig.lane.refuse = TicketRefusal.LANE_UNAVAILABLE
    assert refused(rig).refusal is TicketRefusal.LANE_UNAVAILABLE
    (step,) = rig.held
    text, resend = human_actions.render(
        codes.LANE_UNAVAILABLE, path="a", effect="up", subject="(unspecified)"
    )
    assert step == StepView(
        LINEAGE, NOW, StepKind.BLOCKED, codes.LANE_UNAVAILABLE, text, resend, None
    )
    assert resend is Resend.UNKNOWN and rig.lane.steps == [] and rig.flips == []

    # IN_DOUBT: Blocked(EFFECT_UNCONFIRMED) naming the node's unresolved latest effect, recorded
    rig = Rig()
    rig.lane.refuse = TicketRefusal.IN_DOUBT
    rig.lane.tickets = (ticket_view("restart", ConfirmationStatus.UNKNOWN),)
    assert refused(rig).refusal is TicketRefusal.IN_DOUBT
    (step,) = rig.lane.steps
    assert (step.kind, step.code, step.handle) == (StepKind.BLOCKED, codes.EFFECT_UNCONFIRMED, None)
    assert step.human_action is not None and "restart" in step.human_action
    assert step.resend is Resend.UNKNOWN and rig.held == [] and rig.flips == []
    # with no unresolved ticket to name, the requested effect is named
    rig = Rig()
    rig.lane.refuse = TicketRefusal.IN_DOUBT
    refused(rig)
    assert rig.lane.steps[0].human_action is not None and "up" in rig.lane.steps[0].human_action

    # the defects: StepEntry(FAILED, UNIT_RAISED) recorded and the root flips (B1-E6)
    rig = Rig()
    rig.lane.refuse = TicketRefusal.PLAN_NOT_RECORDED
    assert refused(rig).refusal is TicketRefusal.PLAN_NOT_RECORDED
    (step,) = rig.lane.steps
    assert (step.kind, step.code, step.human_action, step.resend) == (
        StepKind.FAILED,
        codes.UNIT_RAISED,
        None,
        None,
    )
    assert rig.flips == [1]

    # step (1): an undeclared id and a wrong facet class, before any descriptor or ticket
    rig = Rig()
    assert refused(rig, "no-such-effect").refusal is TicketRefusal.UNDECLARED_EFFECT
    assert rig.kinds() == ["step", "flip"] and rig.lane.steps[0].code == codes.UNIT_RAISED
    rig = Rig()
    with pytest.raises(EffectRefused) as raised:
        rig.effects().owned(ports.ResourceOwned).restart(HANDLE, "up")  # "up" is a CREATE effect
    assert raised.value.refusal is TicketRefusal.WRONG_FACET_CLASS
    assert rig.kinds() == ["step", "flip"] and rig.goal is Goal.RELEASE

    # RELEASE_POINT_PASSED at issue records nothing (test_goal_release_refuses_non_release)
    # and after the ticket records NOT_APPLIED(STOP_SEEN) (test_stop_flag_after_ticket_...)


def test_in_doubt_blocked_step_held_when_lane_refuses() -> None:
    for full in (svc.LaneRefusal.FULL, svc.LaneRefusal.UNAVAILABLE):
        rig = Rig()
        rig.lane.refuse = TicketRefusal.IN_DOUBT
        rig.lane.step_refusal = full
        refused(rig)
        assert len(rig.lane.steps) == 1  # offered to the lane once
        assert rig.held == rig.lane.steps  # and, refused, handed to the hold callback
        assert rig.held[0].code == codes.EFFECT_UNCONFIRMED
    # a lane that takes it keeps it: nothing is held
    rig = Rig()
    rig.lane.refuse = TicketRefusal.IN_DOUBT
    refused(rig)
    assert rig.held == []
    # from release the step carries the handle as the handle's cleanup outcome
    rig = Rig()
    rig.lane.refuse = TicketRefusal.IN_DOUBT
    with pytest.raises(EffectRefused):
        ReleaseBinder(rig.context(), HANDLE).owned(ports.ResourceOwned).stop(HANDLE, "stop")
    assert rig.lane.steps[0].handle == HANDLE
    # LANE_UNAVAILABLE never reaches a lane that cannot take it, even one that is only FULL
    rig = Rig()
    rig.lane.refuse = TicketRefusal.LANE_UNAVAILABLE
    rig.lane.step_refusal = svc.LaneRefusal.FULL
    refused(rig)
    assert rig.lane.steps == [] and len(rig.held) == 1
    # an unexpected lane refusal of a step is a loop defect
    rig = Rig()
    rig.lane.refuse = TicketRefusal.IN_DOUBT
    rig.lane.step_refusal = svc.LaneRefusal.OVER_BOUND
    with pytest.raises(RuntimeError, match="over_bound"):
        rig.effects().create(ports.ResourceCreate).create(SPEC, "up")


# ------------------------------------------------------------------------------ V-10 forms

ARGV = ports.ArgvRelease(
    executable="/usr/bin/docker",
    observe_argv=("ps", "-aq", "--filter", "name=x"),
    observe_ok_exit=frozenset({0}),
    stop_argv=("stop", "x"),
    timeout=timedelta(seconds=12.5),
    remove_argv=("rm", "x"),
)
FORMS = (
    GROUP,
    ports.InRunGroup(helpers_disclosed=True),
    ports.Durable(ports.DurableOwner.HOST),
    ports.Durable(ports.DurableOwner.ENVIRONMENT),
    ARGV,
    ports.ArgvRelease("/x", (), frozenset(), (), timedelta(seconds=1)),
)


@pytest.mark.parametrize("form", FORMS)
def test_descriptor_wire_roundtrip_v10_forms(form: ports.ReleaseDescriptor) -> None:
    wire = ports.descriptor_to_wire(form)
    assert ports.descriptor_from_wire(wire) == form
    assert ports.descriptor_from_wire(json.loads(json.dumps(wire))) == form  # JSON-safe
    assert ports.as_descriptor(wire) == form and ports.as_descriptor(form) is form
    # the wire mapping is the one the attempt lane records, and the lane reads either form back
    lane_form = rs.descriptor_to_lane(form)
    assert lf.descriptor_record(lane_form) == wire | (
        {"remove_argv": None} if wire["form"] == "argv" and "remove_argv" not in wire else {}
    )
    assert rs.descriptor_to_lane(wire) == lane_form


@pytest.mark.parametrize(
    "wire",
    [
        {},
        {"form": "docker"},
        {"form": "durable"},
        {"form": "durable", "owner": "nobody"},
        {"form": "durable", "owner": "host", "extra": 1},
        {"form": "in_run_group"},
        {"form": "in_run_group", "helpers_disclosed": "no"},
        {"form": "in_run_group", "helpers_disclosed": 0},
        {"form": "argv", "executable": "/x"},
        {
            "form": "argv",
            "executable": "",
            "observe_argv": [],
            "observe_ok_exit": [],
            "stop_argv": [],
            "timeout_s": 1,
        },
        {
            "form": "argv",
            "executable": "/x",
            "observe_argv": "ps",
            "observe_ok_exit": [0],
            "stop_argv": [],
            "timeout_s": 1,
        },
        {
            "form": "argv",
            "executable": "/x",
            "observe_argv": [],
            "observe_ok_exit": [True],
            "stop_argv": [],
            "timeout_s": 1,
        },
        {
            "form": "argv",
            "executable": "/x",
            "observe_argv": [],
            "observe_ok_exit": [0],
            "stop_argv": [],
            "timeout_s": 0,
        },
        {
            "form": "argv",
            "executable": "/x",
            "observe_argv": [],
            "observe_ok_exit": [0],
            "stop_argv": [],
            "timeout_s": 1,
            "surprise": 1,
        },
    ],
)
def test_descriptor_wire_mapping_matching_no_form_is_refused(wire: dict[str, Any]) -> None:
    with pytest.raises(ports.DescriptorError):
        ports.descriptor_from_wire(wire)


def test_descriptor_from_wire_refuses_non_mapping_and_unknown_objects() -> None:
    for junk in (None, "in_run_group", 3, ["form"]):
        with pytest.raises(ports.DescriptorError):
            ports.descriptor_from_wire(junk)  # type: ignore[arg-type]
        with pytest.raises(ports.DescriptorError):
            ports.as_descriptor(junk)


# ------------------------------------------------------------------------------ the binders


def test_read_and_effect_facets_are_distinct_types() -> None:
    rig = Rig()

    class Reads:
        def observe(self, spec: object, lineage: object, effect: object) -> str:
            return "observed"

        def create(self, spec: object, ticket: object) -> str:  # an effect member on a composite
            return "created"

    ctx = rig.context(only_ports={ports.ResourceReads: Reads()})
    reads = ReadBinder(ctx).read(ports.ResourceReads)
    assert reads.observe(SPEC, LINEAGE, None) == "observed"  # type: ignore[arg-type]
    with pytest.raises(AttributeError):  # the composite's effect member is not reachable
        reads.create  # noqa: B018
    with pytest.raises(AttributeError):
        reads.observe = None  # type: ignore[method-assign, misc]
    assert not hasattr(ReadBinder, "create") and not hasattr(ReadBinder, "owned")
    assert set(dir(ReleaseBinder)) & {"read", "create", "event", "safe_start"} == set()
    # a protocol of the wrong family is refused at binding, an unbound port by name
    with pytest.raises(TypeError):
        ReadBinder(ctx).read(ports.ResourceCreate)
    with pytest.raises(TypeError):
        EffectBinder(rig.context()).create(ports.ResourceOwned)
    with pytest.raises(PortNotBound):
        EffectBinder(rig.context()).safe_start(ports.SafeStartFacet)
    with pytest.raises(TypeError):
        ReleaseBinder(rig.context(), object())  # type: ignore[arg-type]


def test_facet_class_markers_and_pure_members() -> None:
    assert ports.EffectFacet not in ports.ResourceReads.__mro__
    for effect_protocol in (ports.ResourceCreate, ports.ResourceOwned, ports.ExecutionPort):
        assert ports.ReadFacet not in effect_protocol.__mro__
        assert ports.EffectFacet in effect_protocol.__mro__
    rig = Rig()
    create = rig.effects().create(ports.ResourceCreate)
    # a pure member takes no ticket and is passed through; a name outside the port is refused
    assert create.launch_policy(SPEC).helpers is ports.Helpers.PREVENTED
    assert rig.log == []
    with pytest.raises(AttributeError):
        create.not_a_member  # noqa: B018
    assert ports.ExecutionResult(0, ports.ExecutionClass.PASSED, None, (), None, "").recorded.passed
    assert not ports.ExecutionResult(
        2, ports.ExecutionClass.INTERRUPTED, None, (), "x", ""
    ).recorded.passed


def test_ticketed_accepts_effect_by_keyword_and_position() -> None:
    rig = Rig()
    create = rig.effects().create(ports.ResourceCreate)
    create.create(SPEC, effect="up")
    create.create(spec=SPEC, effect="up")
    assert [e[0] for e in rig.log].count("port") == 2
