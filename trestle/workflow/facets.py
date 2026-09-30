"""Ticketed facets (L.SV-5.6; B1-C6, B1-E4, B1-E6, V-4.7, V-5.4, V-5.5).

The loop builds one binder per unit call and hands the unit its facets: read facets
(`ReadBinder.read`), effect facets (`EffectBinder`: create / owned / safe_start / event) and, for a
release call, release facets (`ReleaseBinder`: owned only, for one handle). A facet is resolved by
port protocol type alone (`FacetContext.ports`, one implementation per protocol; V-6.1), and the
unit-facing form of an effect protocol, `Ticketed[P]`, is `P` with every `ticket: AttemptTicket`
parameter replaced by `effect: EffectId`. Calling it runs B1-C6's steps in order:

1. the effect is declared on this node with the facet class requested (the only such check);
2. `P.release_descriptor(EffectCall)`, the declared `lifetime` and `release_timeout` copied
   unchanged (the one descriptor source, V-5.4; no branch on port, kind or lifetime);
3. `AttemptLane.issue`, durable before it returns, with that descriptor, the declared facet, the
   leaf's `max_attempts` and `remedy` (the granted remedy for its own effect only); a goal already
   `RELEASE` issues nothing for a non-release effect;
3a. for a non-release effect, a stop check: a stop flag or a goal already `RELEASE` records
   `Confirmation(NOT_APPLIED, STOP_SEEN)`, the loop flips the goal (`FacetContext.flip_goal`),
   the port is never called, and `EffectRefused(RELEASE_POINT_PASSED)` is raised;
4. the port call;
5. the confirmation (a `CreatedHandle` only for an APPLIED CREATE, decided by the lane), and for
   an event the projection `R.recorded` (V-5.5). A port that raises after step 3 has
   `Confirmation(UNKNOWN)` recorded first (B1-E6), then the exception reaches the unit.

Every refusal records its B1-E4 outcome before `EffectRefused` is raised, so the outcome is the
same whether the unit catches it or lets it escape. A step the lane cannot hold goes to the loop's
hold callback (`FacetContext.hold`, V-4.5); `LANE_UNAVAILABLE`'s Blocked step never reaches the
lane (it cannot take it).
"""

from __future__ import annotations

import functools
import inspect
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Generic, NoReturn

from trestle.workflow import codes, human_actions
from trestle.workflow.declarations import (
    EffectDeclaration,
    EffectFacetClass,
    EffectId,
    HumanAction,
    LeafDeclaration,
    StableCode,
)
from trestle.workflow.ports import (
    CreateFacet,
    EffectCall,
    EventFacet,
    OwnedEffectFacet,
    ReadFacet,
    SafeStartFacet,
    as_descriptor,
)
from trestle.workflow.services import AttemptLane, LaneRefusal
from trestle.workflow.units import EffectRefused, StepView
from trestle.workflow.values import (
    CancelSignal,
    Confirmation,
    ConfirmationStatus,
    CreatedHandle,
    Goal,
    Instant,
    Lineage,
    OwnedHandle,
    RemedyGrant,
    Resend,
    StepKind,
    TicketRefusal,
)


class PortNotBound(LookupError):
    """No implementation is bound for the requested port protocol (a wiring defect: the unit
    raised, B1-E6)."""


class PortContractViolation(RuntimeError):
    """A port returned a shape its contract forbids (V-5.5): raised after the confirmation is
    recorded, so the record still shows the attempt as issued and resolved."""


@dataclass(frozen=True, slots=True)
class FacetContext:
    """Everything a binder needs from the loop for one unit call.

    `ports` maps a port protocol type to its one implementation. `goal` and `flip_goal` read and
    flip the root's goal (`flip_goal` is idempotent; it is the loop's, B1-E6). `hold` is the loop's
    hold callback for steps the lane refused to hold (V-4.5). `remedy` is the remedy the loop
    granted for this call, if any (`ActContext.remedy`)."""

    lane: AttemptLane
    lineage: Lineage
    declaration: LeafDeclaration
    ports: Mapping[type, object]
    cancellation: CancelSignal
    goal: Callable[[], Goal]
    flip_goal: Callable[[], None]
    hold: Callable[[StepView], None]
    now: Callable[[], Instant]
    remedy: RemedyGrant | None = None


def _members(port: type) -> frozenset[str]:
    """The public names a port protocol (and its Protocol bases) declares."""
    names: set[str] = set()
    for cls in port.__mro__:
        if cls in (object, Generic) or cls.__name__ == "Protocol":
            continue
        names.update(n for n in vars(cls) if not n.startswith("_"))
    return frozenset(names)


def _implementation(ctx: FacetContext, port: type, marker: type) -> object:
    if marker not in port.__mro__:
        raise TypeError(f"{port.__name__} is not a {marker.__name__} protocol")
    try:
        return ctx.ports[port]
    except KeyError:
        raise PortNotBound(f"no implementation is bound for {port.__name__}") from None


class ReadView:
    """A read facet: the port's own members and nothing else, so a composite adapter that also
    implements effect protocols exposes no effect member through a read facet (B3-I2)."""

    __slots__ = ("_impl", "_names")

    def __init__(self, impl: object, port: type) -> None:
        object.__setattr__(self, "_impl", impl)
        object.__setattr__(self, "_names", _members(port))

    def __getattr__(self, name: str) -> Any:
        if name not in self._names:
            raise AttributeError(f"{name!r} is not a member of this read facet")
        return getattr(self._impl, name)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("a read facet is read-only")


def _status(confirmation: Confirmation) -> ConfirmationStatus:
    """The status as the vocabulary's enum: a stdlib-only fake returns its own enum with the same
    values (DM-09, structural conformance)."""
    return ConfirmationStatus(getattr(confirmation.status, "value", confirmation.status))


def _path_text(lineage: Lineage) -> str:
    return "/".join(lineage.path.segments) or "(root)"


class Ticketed[P]:
    """`P` with every `ticket: AttemptTicket` parameter replaced by `effect: EffectId`."""

    __slots__ = ("_ctx", "_facet", "_handle", "_impl", "_names", "_projects_result")

    def __init__(
        self,
        ctx: FacetContext,
        impl: object,
        port: type,
        facet: EffectFacetClass,
        handle: CreatedHandle | None,
        projects_result: bool = False,
    ) -> None:
        object.__setattr__(self, "_projects_result", projects_result)
        object.__setattr__(self, "_ctx", ctx)
        object.__setattr__(self, "_impl", impl)
        object.__setattr__(self, "_names", _members(port))
        object.__setattr__(self, "_facet", facet)
        object.__setattr__(self, "_handle", handle)

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("a ticketed facet is read-only")

    def __getattr__(self, name: str) -> Any:
        if name not in self._names:
            raise AttributeError(f"{name!r} is not a member of this facet")
        member = getattr(self._impl, name)
        signature = inspect.signature(member)
        if "ticket" not in signature.parameters:
            return member  # a pure member (policy, launch_policy, release_descriptor)

        @functools.wraps(member)
        def call(*args: Any, **kwargs: Any) -> Any:
            return self._effect_call(name, member, signature, args, kwargs)

        return call

    # ------------------------------------------------------------------ B1-C6, in order

    def _effect_call(
        self,
        name: str,
        member: Callable[..., Any],
        signature: inspect.Signature,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> Any:
        ctx: FacetContext = self._ctx
        unit_params = [
            p.replace(name="effect", annotation=str) if p.name == "ticket" else p
            for p in signature.parameters.values()
        ]
        bound = signature.replace(parameters=unit_params).bind(*args, **kwargs)
        bound.apply_defaults()
        arguments = dict(bound.arguments)
        effect: EffectId = arguments.pop("effect")

        # (1) declared id and facet class: the only such check
        declared = declared_effect(ctx.declaration, effect)
        if declared is None:
            self._defect(TicketRefusal.UNDECLARED_EFFECT)
        if not self._is_requested_class(declared):
            self._defect(TicketRefusal.WRONG_FACET_CLASS)

        # (2) the one descriptor source, before the ticket and the port call
        call = EffectCall(
            member=name,
            arguments=arguments,
            lineage=ctx.lineage,
            effect=effect,
            lifetime=declared.lifetime,
            release_timeout=declared.release_timeout,
        )
        release = as_descriptor(self._impl.release_descriptor(call))

        # (3) the ticket, durable before it returns
        if ctx.goal() is Goal.RELEASE and not declared.is_release:
            raise EffectRefused(TicketRefusal.RELEASE_POINT_PASSED)
        remedy = ctx.remedy if ctx.remedy is not None and ctx.remedy.effect == effect else None
        issued = ctx.lane.issue(
            ctx.lineage,
            effect,
            declared.facet,
            ctx.declaration.flags.repeat,
            declared.lifetime,
            release,
            ctx.declaration.max_attempts,
            remedy,
        )
        if isinstance(issued, TicketRefusal):
            self._refused(issued, effect)
        ticket = issued

        # (3a) a stop after the ticket is durable: NOT_APPLIED, no port call
        if not declared.is_release and (ctx.cancellation.requested or ctx.goal() is Goal.RELEASE):
            ctx.lane.confirm(
                ticket, Confirmation(ConfirmationStatus.NOT_APPLIED, codes.STOP_SEEN, None)
            )
            ctx.flip_goal()
            raise EffectRefused(TicketRefusal.RELEASE_POINT_PASSED)

        # (4) the port call
        try:
            result = member(**arguments, ticket=ticket)
        except Exception:
            ctx.lane.confirm(ticket, Confirmation(ConfirmationStatus.UNKNOWN, None, None))
            raise

        # (5) the confirmation; an event's projected result (V-5.5)
        if self._projects_result:
            confirmation, outcome = result
            ctx.lane.confirm(ticket, confirmation)
            if outcome is not None:
                ctx.lane.record_result(ticket, outcome.recorded)
            elif _status(confirmation) != ConfirmationStatus.NOT_APPLIED:
                raise PortContractViolation(
                    f"{name} returned no result for a {_status(confirmation).value} event (V-5.5)"
                )
        else:
            ctx.lane.confirm(ticket, result)
        return result

    def _is_requested_class(self, declared: EffectDeclaration) -> bool:
        """Step (1)'s facet-class check: the one place the facet class the unit asked for
        (`create`/`owned`/`safe_start`/`event`) meets the class the node declared."""
        return declared.facet is self._facet

    # ------------------------------------------------------------------ B1-E4's outcomes

    def _refused(self, refusal: TicketRefusal, effect: EffectId) -> NoReturn:
        ctx: FacetContext = self._ctx
        if refusal is TicketRefusal.LANE_UNAVAILABLE:
            # the lane cannot take the step: the loop keeps it in process (V-4.5)
            ctx.hold(self._blocked(codes.LANE_UNAVAILABLE, effect))
        elif refusal is TicketRefusal.IN_DOUBT:
            step = self._blocked(codes.EFFECT_UNCONFIRMED, self._unconfirmed_effect(effect))
            self._record(step)
        elif refusal is TicketRefusal.PLAN_NOT_RECORDED:
            self._defect(refusal)
        # ONCE_ALREADY_ISSUED, ATTEMPTS_SPENT, RELEASE_POINT_PASSED: nothing new
        raise EffectRefused(refusal)

    def _defect(self, refusal: TicketRefusal) -> NoReturn:
        """A defect in the unit or the loop: `StepEntry(FAILED, UNIT_RAISED)`, the root flips
        (B1-E4, B1-E6), then the refusal is raised."""
        ctx: FacetContext = self._ctx
        self._record(self._step(StepKind.FAILED, codes.UNIT_RAISED, None, None))
        ctx.flip_goal()
        raise EffectRefused(refusal)

    def _unconfirmed_effect(self, requested: EffectId) -> EffectId:
        """The unconfirmed effect IN_DOUBT names: the node's latest ticket while it is unresolved
        (V-4.7), else the requested one."""
        ctx: FacetContext = self._ctx
        tickets = ctx.lane.node_record(ctx.lineage.path).tickets
        if tickets:
            latest = tickets[-1]
            conf = latest.confirmation
            if conf is None or conf.status is ConfirmationStatus.UNKNOWN:
                return latest.effect
        return requested

    def _step(
        self,
        kind: StepKind,
        code: StableCode,
        human_action: HumanAction | None,
        resend: Resend | None,
    ) -> StepView:
        ctx: FacetContext = self._ctx
        return StepView(ctx.lineage, ctx.now(), kind, code, human_action, resend, self._handle)

    def _blocked(self, code: StableCode, effect: EffectId) -> StepView:
        text, resend = human_actions.render(
            code, path=_path_text(self._ctx.lineage), effect=effect, subject="(unspecified)"
        )
        return self._step(StepKind.BLOCKED, code, text, resend)

    def _record(self, step: StepView) -> None:
        """Through the lane's `record_step`; a `FULL` or `UNAVAILABLE` return goes to the hold
        callback (V-4.5, B2-C7). Any other refusal is a loop defect."""
        refused = self._ctx.lane.record_step(step)
        if refused is None:
            return
        if refused in (LaneRefusal.FULL, LaneRefusal.UNAVAILABLE):
            self._ctx.hold(step)
            return
        raise RuntimeError(f"the lane refused a facet step: {refused.value}")


# ------------------------------------------------------------------------------- binders

_EFFECT_MARKERS: dict[EffectFacetClass, type] = {
    EffectFacetClass.CREATE: CreateFacet,
    EffectFacetClass.OWNED: OwnedEffectFacet,
    EffectFacetClass.SAFE_START: SafeStartFacet,
    EffectFacetClass.EVENT: EventFacet,
}


class ReadBinder:
    """`ReadFacets`: only `read`. A read facet is a different type from an effect facet."""

    def __init__(self, ctx: FacetContext) -> None:
        self._ctx = ctx

    def read[R](self, port: type[R]) -> R:
        return ReadView(_implementation(self._ctx, port, ReadFacet), port)  # type: ignore[return-value]


class EffectBinder(ReadBinder):
    """`EffectFacets`: the read facets plus the four ticketed effect facets, for `advance`."""

    def _ticketed[E](
        self,
        port: type[E],
        facet: EffectFacetClass,
        *,
        projects_result: bool = False,
    ) -> Ticketed[E]:
        impl = _implementation(self._ctx, port, _EFFECT_MARKERS[facet])
        return Ticketed(self._ctx, impl, port, facet, None, projects_result)

    def create[C](self, port: type[C]) -> Ticketed[C]:
        return self._ticketed(port, EffectFacetClass.CREATE)

    def owned[O](self, port: type[O]) -> Ticketed[O]:
        return self._ticketed(port, EffectFacetClass.OWNED)

    def safe_start[S](self, port: type[S]) -> Ticketed[S]:
        return self._ticketed(port, EffectFacetClass.SAFE_START)

    def event[E](self, port: type[E]) -> Ticketed[E]:
        return self._ticketed(port, EffectFacetClass.EVENT, projects_result=True)


class ReleaseBinder:
    """`ReleaseFacets` for one handle: only `owned`. A step the facet records (IN_DOUBT) carries
    that handle as its cleanup outcome (B1-E4)."""

    def __init__(self, ctx: FacetContext, handle: OwnedHandle) -> None:
        if not isinstance(handle, CreatedHandle):
            raise TypeError("a release facet is bound to a CreatedHandle (V-4.2)")
        self._ctx = ctx
        self._handle = handle

    def owned[O](self, port: type[O]) -> Ticketed[O]:
        impl = _implementation(self._ctx, port, OwnedEffectFacet)
        return Ticketed(self._ctx, impl, port, EffectFacetClass.OWNED, self._handle)


def declared_effect(declaration: LeafDeclaration, effect: EffectId) -> EffectDeclaration | None:
    """The leaf's declaration of `effect`, if any (B1-C6 step 1)."""
    return next((e for e in declaration.effects if e.effect == effect), None)
