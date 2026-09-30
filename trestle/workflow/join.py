"""The pure join: the one place a node's verdict is produced (V-3; L.SV-5.4).

`join(terms, observation, record, host_scope, clock) -> Verdict` evaluates V-3.1's rows in V-3.6's
group order and the first matching group gives the condition. Trestle-owned: no unit, composite or
step result can mark a node satisfied (V-3.3). Provenance comes only from the record x the
observation (V-3.2, V-3.6 last line). Pure: no I/O, no clock read (`clock` is an argument), no
module state. The record is read only through `units.NodeRecordView` (MC-B2-02), never a second
definition of V-4's entry shape.

Groups 1, 3 and the `RECORDED` rows of group 4 plus J-24 are L.SV-5.4's; the `OBSERVED` rows of
group 4, J-25/J-25a (group 5) and J-15 (group 6) are L.SV-5.5's; group 5a (J-3, J-3a, the remedy
path) is L.SL-6.1's.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from trestle.workflow import codes, human_actions
from trestle.workflow.declarations import CompletionSource, RemedyDeclaration, Repeat
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

    @property
    def wait_anchor(self) -> datetime | None:
        """V-3.1 "within its wait": a NoAction step recorded after the latest ticket (or with no
        ticket) when one stands, else the latest ticket's `issued_at`."""
        no_action = [s for s in self.steps_after if s.kind is StepKind.NO_ACTION]
        if no_action:
            return no_action[-1].at
        return self.latest.issued_at if self.latest is not None else None

    def wait_elapsed(self, anchor: datetime | None) -> bool:
        return anchor is not None and self.clock.now >= anchor + self.terms.wait.max_wait

    @property
    def within_slice(self) -> bool:
        return self.clock.now < self.terms.slice_end

    def remedy_used(self, code: str) -> int:
        return sum(1 for t in self.record.tickets if t.remedy is not None and t.remedy.code == code)

    def remedy_issuable(self, code: str) -> RemedyDeclaration | None:
        """V-3.1 "a remedy is issuable for code c": a declared remedy for c has attempts left (a
        raw count), and the lane would ticket its effect: the leaf is SAFE, or the record holds no
        ticket for that effect other than ones resolved NOT_APPLIED."""
        for decl in self.terms.remedies:
            if decl.code != code or self.remedy_used(code) >= decl.attempts:
                continue
            if self.terms.flags.repeat is Repeat.SAFE:
                return decl
            if not any(
                t.effect == decl.effect
                and not (
                    t.confirmation is not None
                    and t.confirmation.status is ConfirmationStatus.NOT_APPLIED
                )
                for t in self.record.tickets
            ):
                return decl
        return None

    def first_remedy(self, trigger_codes: tuple[str, ...]) -> RemedyGrant | None:
        for code in trigger_codes:
            decl = self.remedy_issuable(code)
            if decl is not None:
                return RemedyGrant(decl.code, decl.effect, self.remedy_used(code) + 1)
        return None

    def new_ticket_issuable(self, trigger_codes: tuple[str, ...]) -> bool:
        """V-3.1 "a new ticket is issuable for c": a remedy is issuable for c, or the leaf is SAFE
        with attempts left."""
        if self.first_remedy(trigger_codes) is not None:
            return True
        return self.terms.flags.repeat is Repeat.SAFE and self.attempts_left

    def reading(self, subject: object) -> str | None:
        """The latest host-scope reading for `subject`, or None when it has none (V-9.6, V-9.7)."""
        best: tuple[datetime, str] | None = None
        for ref, generation, observed_at in self.host_scope.readings:
            if ref == subject and (best is None or observed_at >= best[0]):
                best = (observed_at, generation)
        return best[1] if best is not None else None

    def differs(self, fact: CurrencyFact) -> bool:
        """String inequality with the latest reading; no reading joins as if it differed (V-9.7)."""
        return self.reading(fact.subject) != fact.observed_generation


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


def _timeout_codes(obs: Observation) -> tuple[str, ...]:
    """The codes J-20 and J-23 read: the observation's own code, then POSTCONDITION_TIMEOUT."""
    return (*((obs.code,) if obs.code is not None else ()), codes.POSTCONDITION_TIMEOUT)


def _confirmed_rows(ctx: _Ctx, obs: Observation, present: bool, provenance: Provenance) -> Verdict:
    """J-22, J-22a, J-21, J-18, J-19, J-20, J-23, J-23a for a confirmed effect (`present` is the
    presence the row reads, V-3.1 "Which presence a row reads"); V-3.1b applies when the
    provenance is FOUND: a confirmed row's UNSATISFIED and J-22a's FAILED become INCOMPATIBLE
    with that row's code and no remedy."""
    found = provenance is Provenance.FOUND
    if not present:  # J-23 / J-23a: the confirmed resource is absent
        if not ctx.wait_elapsed(ctx.wait_anchor):
            return _verdict(ctx, provenance, Condition.CONVERGING)
        trigger = _timeout_codes(obs)
        if ctx.new_ticket_issuable(trigger):
            return _verdict(
                ctx, provenance, Condition.UNSATISFIED, remedy=ctx.first_remedy(trigger)
            )
        return _verdict(ctx, provenance, Condition.FAILED, codes.POSTCONDITION_TIMEOUT)
    older = next((f for f in obs.currency if f.older is not None), None)
    if older is not None and older.older is not None:  # J-22 / J-22a
        code = older.older
        subject = older.subject.value
        if found:
            return _verdict(ctx, provenance, Condition.INCOMPATIBLE, code, subject=subject)
        if ctx.new_ticket_issuable((code,)):
            return _verdict(
                ctx, provenance, Condition.UNSATISFIED, remedy=ctx.first_remedy((code,))
            )
        return _verdict(ctx, provenance, Condition.FAILED, code)
    stale = tuple(f for f in obs.currency if ctx.differs(f))
    if stale:  # J-21
        if ctx.within_slice:
            return _verdict(ctx, provenance, Condition.STALE, currency=stale)
        return _verdict(
            ctx,
            provenance,
            Condition.BLOCKED,
            codes.CURRENCY_UNCONFIRMED,
            currency=stale,
            subject=stale[0].subject.value,
        )
    if obs.postcondition.satisfied:  # J-18
        return _verdict(ctx, provenance, Condition.SATISFIED)
    if not ctx.wait_elapsed(ctx.wait_anchor):  # J-19
        return _verdict(ctx, provenance, Condition.CONVERGING)
    trigger = _timeout_codes(obs)  # J-20: only a remedy re-advances a confirmed effect
    grant = ctx.first_remedy(trigger)
    if grant is None:
        return _verdict(ctx, provenance, Condition.FAILED, codes.POSTCONDITION_TIMEOUT)
    if found:
        return _verdict(ctx, provenance, Condition.INCOMPATIBLE, grant.code)
    return _verdict(ctx, provenance, Condition.UNSATISFIED, remedy=grant)


def _observed_with_ticket(ctx: _Ctx, obs: Observation, latest: TicketView) -> Verdict:
    """J-16, J-17, J-17b, J-17a for an issued, unconfirmed ticket; otherwise the confirmed rows."""
    conf = latest.confirmation
    if conf is None or conf.status is ConfirmationStatus.UNKNOWN:
        if obs.selector_present:  # J-16: CREATED (the loop confirms); condition as J-18..J-23a
            return _confirmed_rows(ctx, obs, True, Provenance.CREATED)
        elapsed = ctx.wait_elapsed(ctx.wait_anchor)
        if ctx.terms.flags.repeat is Repeat.SAFE:
            if not elapsed:  # J-17
                return _verdict(ctx, Provenance.CLAIMED, Condition.CONVERGING)
            if ctx.attempts_left:  # J-17
                return _verdict(ctx, Provenance.CLAIMED, Condition.UNSATISFIED)
            return _verdict(  # J-17b
                ctx, Provenance.CLAIMED, Condition.BLOCKED, codes.EFFECT_UNCONFIRMED
            )
        if not elapsed:  # J-17a: ONCE; the wait-elapsed row below turns it into BLOCKED
            return _verdict(ctx, Provenance.CLAIMED, Condition.IN_DOUBT)
        return _verdict(ctx, Provenance.CLAIMED, Condition.BLOCKED, codes.EFFECT_UNCONFIRMED)
    # confirmed (APPLIED): the node reads selector_present when its record holds a CreatedHandle,
    # else present (V-3.1b)
    if ctx.has_handle:
        return _confirmed_rows(ctx, obs, obs.selector_present, Provenance.CREATED)
    provenance = Provenance.FOUND if obs.present else Provenance.ABSENT
    return _confirmed_rows(ctx, obs, obs.present, provenance)


def _observed_no_ticket(ctx: _Ctx, obs: Observation) -> Verdict:
    """J-10, J-12, J-13, J-13a, J-14, J-11, then J-24 where J-10 gives UNSATISFIED."""
    if not obs.present:
        if any(not result.satisfied for _, result in obs.preconditions):  # J-24
            return _verdict(
                ctx, Provenance.ABSENT, Condition.BLOCKED, codes.PRECONDITION_UNSATISFIED
            )
        return _verdict(ctx, Provenance.ABSENT, Condition.UNSATISFIED)  # J-10
    found = Provenance.FOUND
    if not (obs.identity_proven and obs.configuration_compatible):  # J-12
        return _verdict(ctx, found, Condition.INCOMPATIBLE, codes.FOUND_INCOMPATIBLE)
    older = next((f for f in obs.currency if f.older is not None), None)
    if older is not None and older.older is not None:  # J-13
        return _verdict(
            ctx, found, Condition.INCOMPATIBLE, older.older, subject=older.subject.value
        )
    stale = tuple(f for f in obs.currency if ctx.differs(f))
    if stale:  # J-13a
        if ctx.within_slice:
            return _verdict(ctx, found, Condition.STALE, currency=stale)
        return _verdict(
            ctx,
            found,
            Condition.BLOCKED,
            codes.CURRENCY_UNCONFIRMED,
            currency=stale,
            subject=stale[0].subject.value,
        )
    if not obs.postcondition.satisfied:  # J-14
        return _verdict(ctx, found, Condition.INCOMPATIBLE, codes.FOUND_UNHEALTHY)
    return _verdict(ctx, found, Condition.SATISFIED)  # J-11


def _group4_observed(ctx: _Ctx) -> Verdict:
    obs = ctx.observation
    if obs is None:
        raise ValueError("an OBSERVED leaf is joined only after an observation (V-3.5)")
    if ctx.latest is None:
        return _observed_no_ticket(ctx, obs)
    return _observed_with_ticket(ctx, obs, ctx.latest)


def _group4(ctx: _Ctx) -> Verdict:
    return _group4_recorded(ctx) if ctx.recorded else _group4_observed(ctx)


def _group5(ctx: _Ctx, resolved: Verdict) -> Verdict:
    """J-25 / J-25a: a SATISFIED result whose currency fact's `valid_until` is earlier than the
    root deadline plus the margin. J-25 downgrades it to UNSATISFIED while the node has no ticket
    yet or a new ticket is issuable for CREDENTIAL_LIFETIME_INSUFFICIENT; J-25a gives BLOCKED
    otherwise (disjoint and complete, V-3.6 point 5)."""
    if resolved.condition is not Condition.SATISFIED:
        return resolved
    limit = ctx.clock.root_deadline + ctx.terms.currency_margin
    short = tuple(
        f for f in resolved.currency if f.valid_until is not None and f.valid_until < limit
    )
    if not short:
        return resolved
    trigger = (codes.CREDENTIAL_LIFETIME_INSUFFICIENT,)
    if ctx.latest is None or ctx.new_ticket_issuable(trigger):  # J-25
        return _verdict(
            ctx,
            resolved.provenance,
            Condition.UNSATISFIED,
            currency=resolved.currency,
            remedy=ctx.first_remedy(trigger),
        )
    return _verdict(  # J-25a
        ctx,
        resolved.provenance,
        Condition.BLOCKED,
        codes.CREDENTIAL_LIFETIME_INSUFFICIENT,
        currency=resolved.currency,
        subject=short[0].subject.value,
    )


def _latest_remedy_ticket(ctx: _Ctx) -> TicketView | None:
    return next((t for t in reversed(ctx.record.tickets) if t.remedy is not None), None)


def _ticket_wait_elapsed(ctx: _Ctx, ticket: TicketView) -> bool:
    """V-3.1 "wait elapsed" for `ticket`: the instant is a recorded `NoAction` step's `at` when one
    was recorded after the ticket, else the ticket's `issued_at`."""
    anchor = ticket.issued_at
    for s in ctx.record.steps:
        if s.kind is StepKind.NO_ACTION and s.handle is None and s.at >= anchor:
            anchor = s.at
    return ctx.wait_elapsed(anchor)


def _remedy_spent(ctx: _Ctx, latest: TicketView) -> bool:
    """J-3's "attempts, total time or cooldown exhausted" for the remedy of `latest` (V-14
    RemedyDeclaration): its `attempts` are all ticketed, or `total` has run from its first remedy
    ticket, or another ticket could no longer start inside `total` once `cooldown` had passed
    since the latest one (a remedy has no other bound: the cooldown is the gap between two of its
    tickets, which the loop keeps, L.SL-6.1)."""
    assert latest.remedy is not None
    code = latest.remedy.code
    decl = next((d for d in ctx.terms.remedies if d.code == code), None)
    if decl is None:
        return False
    first = next(t for t in ctx.record.tickets if t.remedy is not None and t.remedy.code == code)
    return (
        ctx.remedy_used(code) >= decl.attempts
        or ctx.clock.now >= first.issued_at + decl.total
        or latest.issued_at + decl.cooldown > first.issued_at + decl.total
    )


def _carries(ctx: _Ctx, resolved: Verdict, code: str) -> bool:
    """V-3.1's diagnostic fingerprint: the verdict groups 3-5 produced still carries the remedy's
    trigger `code`: as its own code, as the code of the remedy it grants, or as the code J-20 /
    J-23 read off the present observation that leaves the postcondition false."""
    if resolved.code == code or (resolved.remedy is not None and resolved.remedy.code == code):
        return True
    obs = ctx.observation
    return obs is not None and obs.code == code and not obs.postcondition.satisfied


def _group5a(ctx: _Ctx, resolved: Verdict) -> Verdict | None:
    """J-3, then J-3a, against the verdict groups 3-5 produced (V-3.6 5a); either one gives its
    condition and J-15 is not evaluated. Neither matches a SATISFIED verdict: a repair that
    succeeds on its last attempt joins SATISFIED and is answered repaired (B4-C3).

    J-3 (`REMEDY_EXHAUSTED`): the latest remedy ticket's remedy is spent (`_remedy_spent`), counted
    only once that ticket's wait has elapsed or it resolved NOT_APPLIED, so a repair still
    converging (J-19) is never cut short; a verdict that grants a remedy for a different declared
    code still has one issuable and is not exhausted. J-3a (`REMEDY_NO_PROGRESS`): the latest ticket
    is a confirmed remedy ticket whose wait has elapsed and the trigger code persists."""
    if resolved.condition is Condition.SATISFIED:
        return None
    latest = _latest_remedy_ticket(ctx)
    if latest is None or latest.remedy is None:
        return None
    conf = latest.confirmation
    settled = (
        conf is not None and conf.status is ConfirmationStatus.NOT_APPLIED
    ) or _ticket_wait_elapsed(ctx, latest)
    other = resolved.remedy is not None and resolved.remedy.code != latest.remedy.code
    if settled and not other and _remedy_spent(ctx, latest):  # J-3
        return _verdict(
            ctx,
            resolved.provenance,
            Condition.BLOCKED,
            codes.REMEDY_EXHAUSTED,
            currency=resolved.currency,
        )
    if (  # J-3a
        latest is ctx.latest
        and conf is not None
        and conf.status is ConfirmationStatus.APPLIED
        and _ticket_wait_elapsed(ctx, latest)
        and _carries(ctx, resolved, latest.remedy.code)
    ):
        return _verdict(
            ctx,
            resolved.provenance,
            Condition.BLOCKED,
            codes.REMEDY_NO_PROGRESS,
            currency=resolved.currency,
        )
    return None


def _group6(ctx: _Ctx, resolved: Verdict) -> Verdict:
    """J-15: a NoAction step (no handle) recorded after the latest ticket, if any, against the
    verdict groups 4 and 5 produced; it overrides only a verdict that is not SATISFIED."""
    no_action = [s for s in ctx.steps_after if s.kind is StepKind.NO_ACTION]
    if not no_action or resolved.condition is Condition.SATISFIED:
        return resolved
    if not ctx.wait_elapsed(no_action[-1].at):
        return _verdict(ctx, resolved.provenance, Condition.CONVERGING, currency=resolved.currency)
    return _verdict(
        ctx,
        resolved.provenance,
        Condition.FAILED,
        codes.POSTCONDITION_TIMEOUT,
        currency=resolved.currency,
    )


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
    resolved = _group3(ctx)  # 3: J-5a, J-4, J-5
    if resolved is None:
        resolved = _group4(ctx)  # 4: J-6..J-9 / J-10..J-23a, then J-24
    resolved = _group5(ctx, resolved)  # 5: J-25, J-25a
    remedied = _group5a(ctx, resolved)  # 5a: J-3, J-3a (L.SL-6.1)
    if remedied is not None:
        return remedied
    return _group6(ctx, resolved)  # 6: J-15
