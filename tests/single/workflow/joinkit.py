"""Builders for join, termination and loop tests (L.SV-5.4 onward): small, explicit values, no
defaults that hide a field a row reads."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from trestle.common.plan.vocabulary import HOST_SCOPE_UNREADABLE
from trestle.workflow import codes
from trestle.workflow.declarations import (
    CompletionSource,
    Compose,
    EffectFacetClass,
    HostScopeRef,
    Lifetime,
    LoopFlags,
    RemedyDeclaration,
    Repeat,
    WaitPolicy,
)
from trestle.workflow.ports import HostScopeUnreadable
from trestle.workflow.units import NodeRecordView, StepView, TicketView
from trestle.workflow.values import (
    CheckResult,
    ClockReading,
    Confirmation,
    ConfirmationStatus,
    CreatedHandle,
    CurrencyFact,
    HostScopeReading,
    Lineage,
    NodePath,
    NodeTerms,
    Observation,
    RecordedResult,
    RemedyGrant,
    Resend,
    StepKind,
)

T0 = datetime(2026, 6, 1, 12, 0, 0, tzinfo=UTC)
LINEAGE = Lineage("root-1", NodePath(("svc", "api")))
ROOT_DEADLINE = T0 + timedelta(seconds=1000)
DEMO = HostScopeRef.DEMO_CREDENTIAL
TOOLS = HostScopeRef.TOOLCHAIN_INSTALLS

OBSERVED = CompletionSource.OBSERVED
RECORDED = CompletionSource.RECORDED
SAFE = Repeat.SAFE
ONCE = Repeat.ONCE


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def terms(
    *,
    completion: CompletionSource = OBSERVED,
    repeat: Repeat = SAFE,
    retryable: tuple[str, ...] = (),
    remedies: tuple[RemedyDeclaration, ...] = (),
    poll: float = 1,
    max_wait: float = 10,
    max_attempts: int = 3,
    slice_end: float = 500,
    margin: float = 0,
    path: NodePath | None = None,
) -> NodeTerms:
    return NodeTerms(
        flags=LoopFlags(Compose.LEAF, completion, repeat),
        retryable=frozenset(retryable),
        remedies=remedies,
        wait=WaitPolicy(
            poll_every=timedelta(seconds=poll), backoff=1.0, max_wait=timedelta(seconds=max_wait)
        ),
        budget=timedelta(seconds=300),
        max_attempts=max_attempts,
        slice_end=at(slice_end),
        path=path,
        currency_margin=timedelta(seconds=margin),
    )


def remedy(code: str = "tool.busy", effect: str = "fix", attempts: int = 2) -> RemedyDeclaration:
    return RemedyDeclaration(
        code=code,
        effect=effect,
        attempts=attempts,
        total=timedelta(seconds=300),
        cooldown=timedelta(seconds=1),
    )


def check(satisfied: bool = True, code: str | None = None) -> CheckResult:
    return CheckResult(satisfied=satisfied, code=code, detail="")


def obs(
    *,
    present: bool | None = None,
    selector_present: bool = False,
    identity: bool = True,
    config: bool = True,
    post: bool = True,
    pre: tuple[bool, ...] = (),
    currency: tuple[CurrencyFact, ...] = (),
    found: int = 0,
    code: str | None = None,
) -> Observation:
    """`present` defaults to `selector_present or bool(found)` (V-3.5)."""
    from trestle.workflow.values import FoundRef

    refs = tuple(FoundRef("db", f"f{i}", at(0)) for i in range(found))
    return Observation(
        present=(selector_present or bool(refs)) if present is None else present,
        selector_present=selector_present,
        identity_proven=identity,
        configuration_compatible=config,
        postcondition=check(post),
        preconditions=tuple((f"pre{i}", check(ok)) for i, ok in enumerate(pre)),
        currency=currency,
        found=refs,
        code=code,
        payload=None,
    )


def conf(
    status: ConfirmationStatus | None, code: str | None = None, identity: str | None = None
) -> Confirmation | None:
    return None if status is None else Confirmation(status, code, identity)


APPLIED = ConfirmationStatus.APPLIED
NOT_APPLIED = ConfirmationStatus.NOT_APPLIED
UNKNOWN = ConfirmationStatus.UNKNOWN


def handle(effect: str = "e1") -> CreatedHandle:
    return CreatedHandle(LINEAGE, effect, f"sel-{effect}", None)


def ticket(
    *,
    attempt: int = 1,
    issued: float = 0,
    status: ConfirmationStatus | None = None,
    code: str | None = None,
    identity: str | None = None,
    with_handle: bool = False,
    result: RecordedResult | None = None,
    remedy_grant: RemedyGrant | None = None,
    effect: str = "e1",
    repeat: Repeat = SAFE,
) -> TicketView:
    return TicketView(
        lineage=LINEAGE,
        effect=effect,
        facet=EffectFacetClass.CREATE,
        attempt=attempt,
        repeat=repeat,
        lifetime=Lifetime.RUN,
        release=None,
        remedy=remedy_grant,
        issued_at=at(issued),
        confirmation=conf(status, code, identity),
        handle=handle(effect) if with_handle else None,
        result=result,
        released_at=None,
        release_outcome=None,
    )


def step(
    kind: StepKind,
    when: float,
    code: str = "unit.said",
    *,
    human_action: str | None = None,
    resend: Resend | None = None,
    with_handle: bool = False,
) -> StepView:
    if kind is StepKind.BLOCKED:
        human_action = human_action if human_action is not None else "Do the thing."
        resend = resend if resend is not None else Resend.SUCCEEDS_AFTER_ACTION
    return StepView(
        lineage=LINEAGE,
        at=at(when),
        kind=kind,
        code=code,
        human_action=human_action,
        resend=resend,
        handle=handle() if with_handle else None,
    )


def record(
    *, tickets: tuple[TicketView, ...] = (), steps: tuple[StepView, ...] = ()
) -> NodeRecordView:
    return NodeRecordView(tickets=tickets, steps=steps)


def clock(now: float = 0, deadline: float = 1000) -> ClockReading:
    return ClockReading(now=at(now), root_deadline=at(deadline), release_point=at(deadline - 30))


def readings(*items: tuple[HostScopeRef, str]) -> HostScopeReading:
    return HostScopeReading(tuple((subject, gen, at(0)) for subject, gen in items))


NO_READINGS = HostScopeReading(())


class StaticScope:
    """A `HostScopeReads` that answers fixed generations (a subject it does not hold is
    unreadable), for a loop that reads its host scope itself."""

    def __init__(self, *items: tuple[HostScopeRef, str]) -> None:
        self._generations = dict(items)

    def read(self, subject: HostScopeRef) -> Any:
        if subject not in self._generations:
            return HostScopeUnreadable(subject, HOST_SCOPE_UNREADABLE)
        return (subject, self._generations[subject], at(0))


TIMEOUT = codes.POSTCONDITION_TIMEOUT
UNCONFIRMED = codes.EFFECT_UNCONFIRMED
