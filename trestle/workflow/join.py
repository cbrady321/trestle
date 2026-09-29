"""The pure join: the one place a node's verdict is produced (V-3; L.SV-5.4).

`join(terms, observation, record, host_scope, clock) -> Verdict` evaluates V-3.1's rows in V-3.6's
group order and the first matching group gives the condition. Trestle-owned: no unit, composite or
step result can mark a node satisfied (V-3.3). Provenance comes only from the record x the
observation (V-3.2, V-3.6 last line). Pure: no I/O, no clock read (`clock` is an argument), no
module state. The record is read only through `units.NodeRecordView` (MC-B2-02), never a second
definition of V-4's entry shape.

This module holds groups 1, 3 and 4 for `RECORDED` leaves plus J-24 (L.SV-5.4); L.SV-5.5 adds
the `OBSERVED` rows, the currency and lifetime rows and J-15; L.SL-6.1 fills group 5a.
"""

from __future__ import annotations

from dataclasses import dataclass

from trestle.workflow import codes, human_actions
from trestle.workflow.declarations import CompletionSource, Repeat
from trestle.workflow.units import NodeRecordView, StepView, TicketView
from trestle.workflow.values import (
    AttemptSummary,
    ClockReading,
    Condition,
    ConfirmationStatus,
    CurrencyFact,
    HostScopeReading,
    NodeTerms,
    Observation,
    Provenance,
    RemedyGrant,
    Resend,
    StepKind,
    Verdict,
)


@dataclass(frozen=True, slots=True)
class _Ctx:
    """The join's inputs, with the record facts every row reads worked out once."""

    terms: NodeTerms
    observation: Observation | None
    record: NodeRecordView
    host_scope: HostScopeReading
    clock: ClockReading
    latest: TicketView | None
    steps_after: tuple[StepView, ...]

    @property
    def recorded(self) -> bool:
        return self.terms.flags.completion is CompletionSource.RECORDED

    @property
    def has_handle(self) -> bool:
        return any(t.handle is not None for t in self.record.tickets)

    @property
    def attempts_left(self) -> bool:
        """V-3.1: the latest ticket's `attempt` < `terms.max_attempts`."""
        return self.latest is not None and self.latest.attempt < self.terms.max_attempts


def _context(
    terms: NodeTerms,
    observation: Observation | None,
    record: NodeRecordView,
    host_scope: HostScopeReading,
    clock: ClockReading,
) -> _Ctx:
    latest = record.tickets[-1] if record.tickets else None
    # A step counts as recorded after the latest ticket when its instant is not earlier than the
    # ticket's (ties resolve toward the step: a frozen model clock stamps both alike, and a step
    # recorded before a ticket in one instant is the V-4.7 IN_DOUBT hand-over, which is bounded
    # by the ticket rows either way). A step with a `handle` is a cleanup outcome and no row
    # reads it (V-3.7).
    after = tuple(
        s for s in record.steps if s.handle is None and (latest is None or s.at >= latest.issued_at)
    )
    return _Ctx(terms, observation, record, host_scope, clock, latest, after)


# ---- rendering (V-3.7 presence rule, V-11.1)


def _path_text(ctx: _Ctx) -> str:
    path = ctx.terms.path
    if path is None and ctx.record.tickets:
        path = ctx.record.tickets[0].lineage.path
    if path is None and ctx.record.steps:
        path = ctx.record.steps[0].lineage.path
    if path is None or not path.segments:
        return "(root)"
    return "/".join(path.segments)


def _verdict(
    ctx: _Ctx,
    provenance: Provenance,
    condition: Condition,
    code: str | None = None,
    *,
    currency: tuple[CurrencyFact, ...] | None = None,
    remedy: RemedyGrant | None = None,
    human_action: str | None = None,
    resend: Resend | None = None,
    subject: str | None = None,
) -> Verdict:
    """Build a verdict; `human_action` and `resend` follow the V-3.7 presence rule: present iff
    the condition is BLOCKED or INCOMPATIBLE, or the code is POSTCONDITION_TIMEOUT or
    EFFECT_UNCONFIRMED. A value the caller supplies (a unit-authored step) is kept; every other
    case is filled from V-11.1."""
    present = (
        condition in (Condition.BLOCKED, Condition.INCOMPATIBLE) or code in codes.PRESENCE_CODES
    )
    if present:
        text, default_resend = human_actions.render(
            code,
            path=_path_text(ctx),
            effect=ctx.latest.effect if ctx.latest is not None else "(none)",
            subject=subject or "(unspecified)",
        )
        human_action = human_action if human_action is not None else text
        resend = resend if resend is not None else default_resend
    else:
        human_action = None
        resend = None
    latest = ctx.latest
    conf = latest.confirmation if latest is not None else None
    obs = ctx.observation
    return Verdict(
        provenance=provenance,
        condition=condition,
        code=code,
        human_action=human_action,
        resend=resend,
        currency=currency if currency is not None else (obs.currency if obs is not None else ()),
        attempts=AttemptSummary(
            attempts=len(ctx.record.tickets),
            last_status=conf.status if conf is not None else None,
            last_code=conf.code if conf is not None else None,
        ),
        remedy=remedy,
        owned=tuple(t.handle for t in ctx.record.tickets if t.handle is not None),
        found=obs.found if obs is not None else (),
    )


# ---- provenance: from the record x the observation only (V-3.2)


def _provenance(ctx: _Ctx) -> Provenance:
    obs = ctx.observation
    latest = ctx.latest
    resolved_not_applied = (
        latest is not None
        and latest.confirmation is not None
        and latest.confirmation.status is ConfirmationStatus.NOT_APPLIED
    )
    if latest is None or resolved_not_applied:
        if resolved_not_applied and ctx.has_handle:
            return Provenance.CREATED
        if ctx.recorded:
            return Provenance.ABSENT
        return Provenance.FOUND if obs is not None and obs.present else Provenance.ABSENT
    if ctx.recorded:
        if any(t.result is not None for t in ctx.record.tickets):
            return Provenance.CREATED  # J-6: the effect ran in this root
        return Provenance.CLAIMED
    conf = latest.confirmation
    if conf is None or conf.status is ConfirmationStatus.UNKNOWN:  # issued, not confirmed
        if obs is not None and obs.selector_present:
            return Provenance.CREATED  # J-16: the loop confirms
        return Provenance.CLAIMED
    if ctx.has_handle:  # confirmed
        return Provenance.CREATED
    return Provenance.FOUND if obs is not None and obs.present else Provenance.ABSENT  # V-3.1b


# ---- groups


def _group1(ctx: _Ctx) -> Verdict | None:
    """J-1, J-2: a unit's Blocked / Failed step recorded after the latest ticket."""
    blocked = [s for s in ctx.steps_after if s.kind is StepKind.BLOCKED]
    if blocked:
        step = blocked[-1]
        return _verdict(
            ctx,
            _provenance(ctx),
            Condition.BLOCKED,
            step.code,
            human_action=step.human_action,
            resend=step.resend,
        )
    failed = [s for s in ctx.steps_after if s.kind is StepKind.FAILED]
    if failed:
        return _verdict(ctx, _provenance(ctx), Condition.FAILED, failed[-1].code)
    return None


def _group3(ctx: _Ctx) -> Verdict | None:
    """J-5a, J-4, J-5 on the latest ticket when the port resolved it NOT_APPLIED."""
    latest = ctx.latest
    if latest is None or latest.confirmation is None:
        return None
    conf = latest.confirmation
    if conf.status is not ConfirmationStatus.NOT_APPLIED:
        return None
    provenance = _provenance(ctx)
    if conf.code in codes.HUMAN_ACTIONABLE:  # J-5a
        return _verdict(ctx, provenance, Condition.BLOCKED, conf.code, subject=conf.identity)
    if ctx.attempts_left and conf.code is not None and conf.code in ctx.terms.retryable:  # J-4
        return _verdict(ctx, provenance, Condition.UNSATISFIED)
    return _verdict(ctx, provenance, Condition.FAILED, conf.code)  # J-5


def _preconditions_unsatisfied(ctx: _Ctx) -> bool:
    obs = ctx.observation
    return obs is not None and any(not result.satisfied for _, result in obs.preconditions)


def _group4_recorded(ctx: _Ctx) -> Verdict:
    """J-6..J-9, then J-24 (for a RECORDED leaf, the preconditions are all the join reads of the
    observation)."""
    results = [t.result for t in ctx.record.tickets if t.result is not None]
    if results:  # J-6
        result = results[-1]
        if result.passed:
            return _verdict(ctx, Provenance.CREATED, Condition.SATISFIED)
        return _verdict(ctx, Provenance.CREATED, Condition.FAILED, result.code)
    if ctx.latest is not None:  # a ticket, no result
        if ctx.terms.flags.repeat is Repeat.ONCE:  # J-8
            return _verdict(ctx, Provenance.CLAIMED, Condition.IN_DOUBT, codes.EFFECT_UNCONFIRMED)
        if ctx.attempts_left:  # J-7
            return _verdict(ctx, Provenance.CLAIMED, Condition.UNSATISFIED)
        return _verdict(  # J-7a
            ctx, Provenance.CLAIMED, Condition.BLOCKED, codes.EFFECT_UNCONFIRMED
        )
    if _preconditions_unsatisfied(ctx):  # J-24 where J-9 would give UNSATISFIED
        return _verdict(ctx, Provenance.ABSENT, Condition.BLOCKED, codes.PRECONDITION_UNSATISFIED)
    return _verdict(ctx, Provenance.ABSENT, Condition.UNSATISFIED)  # J-9


def _group4(ctx: _Ctx) -> Verdict:
    if ctx.recorded:
        return _group4_recorded(ctx)
    raise NotImplementedError("OBSERVED rows J-10..J-23a arrive with L.SV-5.5")


def join(
    terms: NodeTerms,
    observation: Observation | None,
    record: NodeRecordView,
    host_scope: HostScopeReading,
    clock: ClockReading,
) -> Verdict:
    ctx = _context(terms, observation, record, host_scope, clock)
    first = _group1(ctx)  # 1: J-1, J-2   (group 2 is empty: J-3 is in group 5a)
    if first is not None:
        return first
    third = _group3(ctx)  # 3: J-5a, J-4, J-5
    if third is not None:
        return third
    return _group4(ctx)  # 4: J-6..J-9 (RECORDED), then J-24
