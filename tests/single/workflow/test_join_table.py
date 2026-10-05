"""L.SV-5.4 / L.SV-5.5: the pure join over V-3.1's rows in V-3.6's group order."""

from __future__ import annotations

from dataclasses import fields, replace
from datetime import timedelta

import pytest

from tests.single.workflow.joinkit import (
    APPLIED,
    DEMO,
    NO_READINGS,
    NOT_APPLIED,
    ONCE,
    RECORDED,
    ROOT_DEADLINE,
    SAFE,
    TIMEOUT,
    TOOLS,
    UNCONFIRMED,
    UNKNOWN,
    clock,
    conf,
    handle,
    obs,
    readings,
    record,
    remedy,
    step,
    terms,
    ticket,
)
from trestle.workflow import codes
from trestle.workflow.decide import Command, decide
from trestle.workflow.join import join
from trestle.workflow.values import (
    Condition,
    CurrencyFact,
    Goal,
    Observation,
    Provenance,
    RecordedResult,
    RemedyGrant,
    Resend,
    StepKind,
    Verdict,
)

C = Condition
P = Provenance


def run(t, o, r, now=0, host=NO_READINGS) -> Verdict:  # noqa: ANN001
    return join(t, o, r, host, clock(now))


def shape(v: Verdict) -> tuple[Provenance, Condition, str | None]:
    return (v.provenance, v.condition, v.code)


# ---------------------------------------------------------------- L.SV-5.4


def test_rows_J1_to_J9_J5a_and_J24() -> None:
    rec_terms = terms(completion=RECORDED)
    rec_once = terms(completion=RECORDED, repeat=ONCE)

    # J-1: a unit's Blocked step (no handle) after the latest ticket, if any
    v = run(
        rec_terms,
        None,
        record(steps=(step(StepKind.BLOCKED, 5, "realization.absent", human_action="Start it."),)),
    )
    assert shape(v) == (P.ABSENT, C.BLOCKED, "realization.absent")
    assert (v.human_action, v.resend) == ("Start it.", Resend.SUCCEEDS_AFTER_ACTION)

    # J-2: a Failed step (no handle); FAILED carries no human action (presence rule)
    v = run(rec_terms, None, record(steps=(step(StepKind.FAILED, 5, "unit.broke"),)))
    assert shape(v) == (P.ABSENT, C.FAILED, "unit.broke")
    assert (v.human_action, v.resend) == (None, None)

    # J-5a: NOT_APPLIED with a V-11.2 code -> BLOCKED with V-11.1 text, {subject} from identity
    v = run(
        rec_terms,
        None,
        record(
            tickets=(
                ticket(status=NOT_APPLIED, code=codes.CREDENTIAL_INTERACTIVE, identity="demo-user"),
            )
        ),
    )
    assert shape(v) == (P.ABSENT, C.BLOCKED, codes.CREDENTIAL_INTERACTIVE)
    assert v.human_action is not None and "demo-user" in v.human_action
    assert v.resend is Resend.SUCCEEDS_AFTER_ACTION

    # J-4: NOT_APPLIED, attempts left, code retryable -> UNSATISFIED (even for ONCE)
    for t in (
        terms(completion=RECORDED, retryable=("tool.busy",)),
        terms(completion=RECORDED, repeat=ONCE, retryable=("tool.busy",)),
    ):
        v = run(t, None, record(tickets=(ticket(status=NOT_APPLIED, code="tool.busy"),)))
        assert shape(v) == (P.ABSENT, C.UNSATISFIED, None)

    # J-5: NOT_APPLIED, code not retryable, or attempts spent -> FAILED with the port's code
    v = run(
        terms(completion=RECORDED, retryable=("tool.busy",)),
        None,
        record(tickets=(ticket(status=NOT_APPLIED, code="tool.gone"),)),
    )
    assert shape(v) == (P.ABSENT, C.FAILED, "tool.gone")
    v = run(
        terms(completion=RECORDED, retryable=("tool.busy",), max_attempts=2),
        None,
        record(tickets=(ticket(attempt=2, status=NOT_APPLIED, code="tool.busy"),)),
    )
    assert shape(v) == (P.ABSENT, C.FAILED, "tool.busy")

    # J-6: a result recorded in this root
    passed = RecordedResult(True, None, None)
    failed = RecordedResult(False, "test.assertion", None)
    v = run(rec_once, None, record(tickets=(ticket(status=APPLIED, result=passed),)))
    assert v.condition is C.SATISFIED
    v = run(rec_once, None, record(tickets=(ticket(status=APPLIED, result=failed),)))
    assert shape(v)[1:] == (C.FAILED, "test.assertion")

    # J-7 / J-7a: ticket, no result, SAFE: attempts left / attempts spent
    v = run(rec_terms, None, record(tickets=(ticket(attempt=1),)))
    assert shape(v) == (P.CLAIMED, C.UNSATISFIED, None)
    v = run(rec_terms, None, record(tickets=(ticket(attempt=3),)))
    assert shape(v) == (P.CLAIMED, C.BLOCKED, UNCONFIRMED)
    assert v.human_action is not None and "e1" in v.human_action and v.resend is Resend.UNKNOWN

    # J-8: ticket, no result, ONCE -> IN_DOUBT EFFECT_UNCONFIRMED (presence rule: text present)
    v = run(rec_once, None, record(tickets=(ticket(repeat=ONCE),)))
    assert shape(v) == (P.CLAIMED, C.IN_DOUBT, UNCONFIRMED)
    assert v.human_action is not None and v.resend is Resend.UNKNOWN

    # J-9: nothing recorded
    v = run(rec_terms, None, record())
    assert shape(v) == (P.ABSENT, C.UNSATISFIED, None)

    # J-24: no ticket, a declared precondition not satisfied -> BLOCKED, no action runs
    v = run(rec_terms, obs(pre=(True, False)), record())
    assert shape(v) == (P.ABSENT, C.BLOCKED, codes.PRECONDITION_UNSATISFIED)
    assert v.human_action is not None and "(root)" in v.human_action
    assert v.resend is Resend.SUCCEEDS_AFTER_ACTION
    # ...but only where J-9 would give UNSATISFIED: a recorded result or a ticket is untouched
    v = run(rec_once, obs(pre=(False,)), record(tickets=(ticket(status=APPLIED, result=passed),)))
    assert v.condition is C.SATISFIED
    v = run(rec_terms, obs(pre=(False,)), record(tickets=(ticket(attempt=1),)))
    assert v.condition is C.UNSATISFIED
    # all preconditions satisfied (or none declared): J-9
    assert run(rec_terms, obs(pre=(True, True)), record()).condition is C.UNSATISFIED


def test_group_precedence_order_v3_6() -> None:
    t = terms(completion=RECORDED, retryable=("tool.busy",))
    ticket_na = ticket(issued=0, status=NOT_APPLIED, code="tool.busy")

    # group 1 over group 3: J-1 wins although J-4 would match
    v = run(t, None, record(tickets=(ticket_na,), steps=(step(StepKind.BLOCKED, 1, "unit.wait"),)))
    assert shape(v)[1:] == (C.BLOCKED, "unit.wait")
    # J-1 before J-2 in the table's order when both steps stand
    v = run(
        t,
        None,
        record(
            steps=(step(StepKind.FAILED, 1, "unit.broke"), step(StepKind.BLOCKED, 2, "unit.wait"))
        ),
    )
    assert shape(v)[1:] == (C.BLOCKED, "unit.wait")
    # a step recorded before the latest ticket is not read (J-1 needs "after the latest ticket")
    v = run(t, None, record(tickets=(ticket_na,), steps=(step(StepKind.BLOCKED, -5, "old"),)))
    assert v.condition is C.UNSATISFIED  # J-4 (group 3)
    # a cleanup step (handle set) is never read by J-1 or J-2
    v = run(t, None, record(steps=(step(StepKind.BLOCKED, 1, "cleanup", with_handle=True),)))
    assert v.condition is C.UNSATISFIED  # J-9
    v = run(t, None, record(steps=(step(StepKind.FAILED, 1, "cleanup", with_handle=True),)))
    assert v.condition is C.UNSATISFIED

    # group 3 over group 4: a NOT_APPLIED latest ticket is J-4/J-5, never J-7 or J-9
    assert run(t, None, record(tickets=(ticket_na,))).condition is C.UNSATISFIED
    # J-5a before J-4 when a code is both actionable and (mis)declared retryable
    t_both = terms(completion=RECORDED, retryable=(codes.TOOLCHAIN_MISSING,))
    v = run(
        t_both,
        None,
        record(tickets=(ticket(status=NOT_APPLIED, code=codes.TOOLCHAIN_MISSING, identity="jq"),)),
    )
    assert v.condition is C.BLOCKED

    # within group 4 (RECORDED): J-6 over J-7/J-8, and J-24 last
    passed = RecordedResult(True, None, None)
    v = run(
        terms(completion=RECORDED, repeat=ONCE),
        obs(pre=(False,)),
        record(tickets=(ticket(status=UNKNOWN, result=passed), ticket(attempt=2))),
    )
    assert v.condition is C.SATISFIED
    # the latest ticket decides the group-3 test: an earlier NOT_APPLIED ticket is superseded
    v = run(
        t,
        None,
        record(tickets=(ticket_na, ticket(attempt=2, issued=3))),
    )
    assert shape(v) == (P.CLAIMED, C.UNSATISFIED, None)  # J-7, not J-4

    # a step recorded after the latest ticket blocks it (J-1) even when J-7 would match
    v = run(
        t,
        None,
        record(tickets=(ticket(issued=0),), steps=(step(StepKind.BLOCKED, 4, "unit.wait"),)),
    )
    assert v.condition is C.BLOCKED


def test_join_returns_verdict() -> None:
    passed = RecordedResult(True, None, None)
    t = terms(completion=RECORDED)
    rec = record(
        tickets=(
            ticket(attempt=1, status=NOT_APPLIED, code="tool.gone", issued=0),
            ticket(attempt=2, issued=2, status=APPLIED, with_handle=True, result=passed),
        )
    )
    v = run(t, obs(found=1), rec)
    assert isinstance(v, Verdict)
    # the summary reads the record only: two tickets, the latest's confirmation
    assert v.attempts.attempts == 2
    assert v.attempts.last_status is APPLIED and v.attempts.last_code is None
    assert v.owned == (handle(),)
    assert len(v.found) == 1  # what observation saw
    assert v.remedy is None
    # decide reads the condition: every condition value is a member of V-3's set
    assert v.condition in set(Condition)
    # pure: the same inputs give equal verdicts; the record view is not mutated
    assert run(t, obs(found=1), rec) == v
    assert rec.tickets[0].confirmation == conf(NOT_APPLIED, "tool.gone")


# ---------------------------------------------------------------- L.SV-5.5

CURRENT = readings((DEMO, "g1"), (TOOLS, "t1"))


def fact(subject=DEMO, generation="g1", valid_until=None, older=None) -> CurrencyFact:  # noqa: ANN001
    return CurrencyFact(subject, generation, valid_until, older)


def test_rows_J10_to_J23a() -> None:
    t = terms(max_wait=10, max_attempts=3, slice_end=500)

    # J-10: none, absent (and J-24 where J-10 would give UNSATISFIED)
    assert shape(run(t, obs(), record())) == (P.ABSENT, C.UNSATISFIED, None)
    v = run(t, obs(pre=(False,)), record())
    assert shape(v) == (P.ABSENT, C.BLOCKED, codes.PRECONDITION_UNSATISFIED)
    # J-11: present, identity and configuration proven, postcondition true, currency current
    v = run(t, obs(found=1, currency=(fact(),)), record(), host=CURRENT)
    assert shape(v) == (P.FOUND, C.SATISFIED, None)
    # J-12: identity or configuration unproven
    for kw in ({"identity": False}, {"config": False}):
        v = run(t, obs(found=1, **kw), record())
        assert shape(v) == (P.FOUND, C.INCOMPATIBLE, codes.FOUND_INCOMPATIBLE)
        assert v.human_action is not None and v.resend is Resend.SUCCEEDS_AFTER_ACTION
    # J-13: a currency fact carries `older`
    old = fact(older=codes.CREDENTIAL_STALE)
    v = run(t, obs(found=1, currency=(old,)), record(), host=CURRENT)
    assert shape(v) == (P.FOUND, C.INCOMPATIBLE, codes.CREDENTIAL_STALE)
    assert v.human_action is not None and "demo_credential" in v.human_action
    # J-13a: generation differs from the latest reading: STALE within the slice, BLOCKED after
    drift = fact(generation="g0")
    v = run(t, obs(found=1, currency=(drift,)), record(), now=10, host=CURRENT)
    assert shape(v) == (P.FOUND, C.STALE, None) and v.currency == (drift,)
    v = run(t, obs(found=1, currency=(drift,)), record(), now=500, host=CURRENT)
    assert shape(v) == (P.FOUND, C.BLOCKED, codes.CURRENCY_UNCONFIRMED)
    assert (
        v.currency == (drift,)
        and v.human_action is not None
        and "demo_credential" in v.human_action
    )
    # an unreadable subject (no reading) joins as if its generation differed (V-9.7)
    v = run(t, obs(found=1, currency=(fact(),)), record(), host=NO_READINGS)
    assert v.condition is C.STALE
    # the latest reading for a subject decides
    late = readings((DEMO, "g0"), (DEMO, "g1"))
    assert run(t, obs(found=1, currency=(fact(),)), record(), host=late).condition is C.SATISFIED
    # J-14: proven identity, postcondition false: up but not ready, never repaired
    v = run(t, obs(found=1, post=False), record())
    assert shape(v) == (P.FOUND, C.INCOMPATIBLE, codes.FOUND_UNHEALTHY)

    # J-16: ticket issued, not confirmed, the selector's instance is present: CREATED, condition
    # as J-18..J-23a (the loop confirms)
    unconfirmed = record(tickets=(ticket(status=None),))
    assert shape(run(t, obs(selector_present=True), unconfirmed)) == (P.CREATED, C.SATISFIED, None)
    assert shape(run(t, obs(selector_present=True, post=False), unconfirmed, now=3)) == (
        P.CREATED,
        C.CONVERGING,
        None,
    )
    # J-17: SAFE, selector absent: CONVERGING within the wait, UNSATISFIED once elapsed
    # with attempts left
    assert shape(run(t, obs(), unconfirmed, now=9)) == (P.CLAIMED, C.CONVERGING, None)
    assert shape(run(t, obs(), unconfirmed, now=10)) == (P.CLAIMED, C.UNSATISFIED, None)
    # a found instance never counts for an unconfirmed ticket (selector_present reads)
    assert run(t, obs(found=1), unconfirmed, now=9).condition is C.CONVERGING
    # J-17b: elapsed with attempts spent
    spent = record(tickets=(ticket(attempt=3, status=UNKNOWN),))
    v = run(t, obs(), spent, now=10)
    assert shape(v) == (P.CLAIMED, C.BLOCKED, UNCONFIRMED) and v.resend is Resend.UNKNOWN
    # J-17a: ONCE: IN_DOUBT within the wait, BLOCKED EFFECT_UNCONFIRMED once elapsed; also when
    # the observation could not be made (V-3.8)
    once = terms(repeat=ONCE, max_wait=10)
    once_rec = record(tickets=(ticket(status=None, repeat=ONCE),))
    assert shape(run(once, obs(), once_rec, now=9)) == (P.CLAIMED, C.IN_DOUBT, None)
    assert shape(run(once, obs(code="docker.cli_missing"), once_rec, now=9))[1] is C.IN_DOUBT
    v = run(once, obs(code="docker.cli_missing"), once_rec, now=10)
    assert shape(v) == (P.CLAIMED, C.BLOCKED, UNCONFIRMED)

    # confirmed rows, an owned resource (the record holds a CreatedHandle: selector_present reads)
    owned = record(tickets=(ticket(status=APPLIED, with_handle=True),))
    # J-18
    assert shape(run(t, obs(selector_present=True), owned)) == (P.CREATED, C.SATISFIED, None)
    # J-19: postcondition false within the wait
    assert shape(run(t, obs(selector_present=True, post=False), owned, now=9)) == (
        P.CREATED,
        C.CONVERGING,
        None,
    )
    # J-20: wait elapsed, no remedy issuable: FAILED POSTCONDITION_TIMEOUT with V-11.1 text
    v = run(t, obs(selector_present=True, post=False), owned, now=10)
    assert shape(v) == (P.CREATED, C.FAILED, TIMEOUT)
    assert v.human_action is not None and v.resend is Resend.UNKNOWN
    # ...a remedy issuable for POSTCONDITION_TIMEOUT or the observation's code: UNSATISFIED + grant
    t_rem = terms(remedies=(remedy(TIMEOUT, "fix", 2),), max_wait=10)
    v = run(t_rem, obs(selector_present=True, post=False), owned, now=10)
    assert shape(v) == (P.CREATED, C.UNSATISFIED, None)
    assert v.remedy == RemedyGrant(TIMEOUT, "fix", 1)
    t_rem2 = terms(remedies=(remedy("tool.busy", "fix", 2),), max_wait=10)
    v = run(t_rem2, obs(selector_present=True, post=False, code="tool.busy"), owned, now=10)
    assert v.condition is C.UNSATISFIED and v.remedy == RemedyGrant("tool.busy", "fix", 1)
    # a remedy whose attempts are spent is not issuable: FAILED
    used = record(
        tickets=(
            ticket(status=APPLIED, with_handle=True),
            ticket(
                attempt=2,
                issued=1,
                status=APPLIED,
                effect="fix",
                remedy_grant=RemedyGrant(TIMEOUT, "fix", 1),
            ),
            ticket(
                attempt=3,
                issued=2,
                status=APPLIED,
                effect="fix",
                remedy_grant=RemedyGrant(TIMEOUT, "fix", 2),
            ),
        )
    )
    v = run(
        terms(remedies=(remedy(TIMEOUT, "fix", 2),), max_attempts=9),
        obs(selector_present=True, post=False),
        used,
        now=20,
    )
    # J-20 finds no remedy issuable (FAILED), and group 5a's J-3 (L.SL-6.1) turns that not-SATISFIED
    # verdict into BLOCKED REMEDY_EXHAUSTED: both of the remedy's attempts are ticketed and the
    # latest remedy ticket's wait has elapsed
    assert shape(v) == (P.CREATED, C.BLOCKED, codes.REMEDY_EXHAUSTED)
    # J-21: no fact older, a generation differs: STALE within the slice, BLOCKED after
    v = run(t, obs(selector_present=True, currency=(drift,)), owned, now=1, host=CURRENT)
    assert shape(v) == (P.CREATED, C.STALE, None)
    v = run(t, obs(selector_present=True, currency=(drift,)), owned, now=500, host=CURRENT)
    assert shape(v) == (P.CREATED, C.BLOCKED, codes.CURRENCY_UNCONFIRMED)
    # J-22: a fact carries `older`, a new ticket is issuable (SAFE, attempts left)
    v = run(t, obs(selector_present=True, currency=(old,)), owned, host=CURRENT)
    assert shape(v) == (P.CREATED, C.UNSATISFIED, None) and v.remedy is None
    t_stale = terms(remedies=(remedy(codes.CREDENTIAL_STALE, "refresh", 1),))
    v = run(t_stale, obs(selector_present=True, currency=(old,)), owned, host=CURRENT)
    assert v.remedy == RemedyGrant(codes.CREDENTIAL_STALE, "refresh", 1)
    # J-22a: no new ticket issuable (ONCE, no remedy): FAILED with the older code
    once_owned = record(tickets=(ticket(status=APPLIED, with_handle=True, repeat=ONCE),))
    v = run(once, obs(selector_present=True, currency=(old,)), once_owned, host=CURRENT)
    assert shape(v) == (P.CREATED, C.FAILED, codes.CREDENTIAL_STALE)
    # J-23: the owned resource is absent: CONVERGING within the wait; UNSATISFIED (it died) once
    # elapsed while a new ticket is issuable; J-23a FAILED when none is
    assert shape(run(t, obs(), owned, now=9)) == (P.CREATED, C.CONVERGING, None)
    assert shape(run(t, obs(), owned, now=10)) == (P.CREATED, C.UNSATISFIED, None)
    v = run(once, obs(), once_owned, now=10)
    assert shape(v) == (P.CREATED, C.FAILED, TIMEOUT)

    # V-3.1b: a confirmed effect with no CreatedHandle reads `present`, and keeps the provenance
    # of the no-ticket rows; a found resource is never advanced toward an owned repair
    noh = record(tickets=(ticket(status=APPLIED, with_handle=False),))
    assert shape(run(t, obs(found=1), noh)) == (P.FOUND, C.SATISFIED, None)
    assert shape(run(t, obs(found=1, post=False), noh, now=3)) == (P.FOUND, C.CONVERGING, None)
    v = run(t, obs(found=1, post=False), noh, now=10)  # J-20 FAILED stays FAILED
    assert shape(v) == (P.FOUND, C.FAILED, TIMEOUT)
    v = run(t_rem, obs(found=1, post=False), noh, now=10)  # UNSATISFIED -> INCOMPATIBLE, no remedy
    assert shape(v) == (P.FOUND, C.INCOMPATIBLE, TIMEOUT) and v.remedy is None
    v = run(t, obs(found=1, currency=(old,)), noh, host=CURRENT)  # J-22 -> INCOMPATIBLE
    assert shape(v) == (P.FOUND, C.INCOMPATIBLE, codes.CREDENTIAL_STALE) and v.remedy is None
    noh_once = record(tickets=(ticket(status=APPLIED, repeat=ONCE),))
    v = run(once, obs(found=1, currency=(old,)), noh_once, host=CURRENT)  # J-22a -> INCOMPATIBLE
    assert shape(v) == (P.FOUND, C.INCOMPATIBLE, codes.CREDENTIAL_STALE)
    # the found instance is gone: provenance ABSENT, the row's condition stands (J-23 / J-23a)
    assert shape(run(t, obs(), noh, now=10)) == (P.ABSENT, C.UNSATISFIED, None)
    assert shape(run(once, obs(), noh_once, now=10)) == (P.ABSENT, C.FAILED, TIMEOUT)
    # a live found instance never makes a dead owned instance join as CREATED / SATISFIED
    v = run(t, obs(selector_present=False, found=1), owned)
    assert shape(v) == (P.CREATED, C.CONVERGING, None)


def test_rows_J25_J25a() -> None:
    short = fact(valid_until=ROOT_DEADLINE - timedelta(seconds=5))
    enough = fact(valid_until=ROOT_DEADLINE + timedelta(seconds=5))
    t = terms(margin=0)
    # no ticket yet: J-25 downgrades a SATISFIED row to UNSATISFIED (one refresh attempt first)
    v = run(t, obs(found=1, currency=(short,)), record(), host=CURRENT)
    assert shape(v) == (P.FOUND, C.UNSATISFIED, None)
    assert run(t, obs(found=1, currency=(enough,)), record(), host=CURRENT).condition is C.SATISFIED
    # the margin counts: valid past the deadline by 5 s is short with a 10 s margin
    assert (
        run(terms(margin=10), obs(found=1, currency=(enough,)), record(), host=CURRENT).condition
        is C.UNSATISFIED
    )
    # a ticket, and a new ticket is issuable (SAFE, attempts left): J-25
    with_ticket = record(tickets=(ticket(status=APPLIED, with_handle=True),))
    v = run(t, obs(selector_present=True, currency=(short,)), with_ticket, host=CURRENT)
    assert shape(v) == (P.CREATED, C.UNSATISFIED, None)
    # ...with a remedy issuable for that code, the grant is set
    t_rem = terms(remedies=(remedy(codes.CREDENTIAL_LIFETIME_INSUFFICIENT, "refresh", 1),))
    v = run(t_rem, obs(selector_present=True, currency=(short,)), with_ticket, host=CURRENT)
    assert v.remedy == RemedyGrant(codes.CREDENTIAL_LIFETIME_INSUFFICIENT, "refresh", 1)
    # J-25a: a ticket, no new ticket issuable (ONCE, no remedy): BLOCKED with V-11.1 text
    once_t = record(tickets=(ticket(status=APPLIED, with_handle=True, repeat=ONCE),))
    v = run(terms(repeat=ONCE), obs(selector_present=True, currency=(short,)), once_t, host=CURRENT)
    assert shape(v) == (P.CREATED, C.BLOCKED, codes.CREDENTIAL_LIFETIME_INSUFFICIENT)
    assert v.human_action is not None and "demo_credential" in v.human_action
    assert v.resend is Resend.SUCCEEDS_AFTER_ACTION
    # ...and SAFE with attempts spent
    spent = record(tickets=(ticket(attempt=3, status=APPLIED, with_handle=True),))
    v = run(t, obs(selector_present=True, currency=(short,)), spent, host=CURRENT)
    assert v.condition is C.BLOCKED
    # only a SATISFIED result is downgraded
    v = run(t, obs(found=1, post=False, currency=(short,)), record(), host=CURRENT)
    assert v.condition is C.INCOMPATIBLE
    # a RECORDED leaf's SATISFIED result carries the observation's facts too
    passed = RecordedResult(True, None, None)
    rec_once = terms(completion=RECORDED, repeat=ONCE)
    v = run(
        rec_once,
        obs(currency=(short,)),
        record(tickets=(ticket(status=APPLIED, result=passed, repeat=ONCE),)),
    )
    assert v.condition is C.BLOCKED


def test_row_J15_override() -> None:
    t = terms(max_wait=10)
    tk = ticket(status=APPLIED, with_handle=True)
    # UNSATISFIED (owned resource absent after its wait) with a NoAction step after the ticket:
    # J-15 gives CONVERGING within the NoAction's own wait, FAILED POSTCONDITION_TIMEOUT after it
    rec = record(tickets=(tk,), steps=(step(StepKind.NO_ACTION, 12, "unit.idle"),))
    v = run(t, obs(), rec, now=12)
    assert shape(v) == (P.CREATED, C.CONVERGING, None)
    assert run(t, obs(), rec, now=21).condition is C.CONVERGING  # anchored at the step (12), not 0
    v = run(t, obs(), rec, now=22)
    assert shape(v) == (P.CREATED, C.FAILED, TIMEOUT)
    assert v.human_action is not None and v.resend is Resend.UNKNOWN
    # with no ticket at all: the step's instant anchors the wait
    rec0 = record(steps=(step(StepKind.NO_ACTION, 2, "unit.idle"),))
    assert shape(run(t, obs(), rec0, now=11)) == (P.ABSENT, C.CONVERGING, None)
    assert shape(run(t, obs(), rec0, now=12)) == (P.ABSENT, C.FAILED, TIMEOUT)
    # a SATISFIED resolved verdict is never overridden
    assert run(t, obs(selector_present=True), rec, now=30).condition is C.SATISFIED
    # a NoAction recorded before the latest ticket is not read
    old_rec = record(tickets=(ticket(issued=20, status=APPLIED, with_handle=True),))
    old_rec = record(tickets=old_rec.tickets, steps=(step(StepKind.NO_ACTION, 1, "unit.idle"),))
    assert run(t, obs(), old_rec, now=25).condition is C.CONVERGING  # J-23 within the ticket wait
    # J-15 also bounds a declined J-25 downgrade (V-3.7) and a retryable J-4 (bounded alike)
    short = fact(valid_until=ROOT_DEADLINE - timedelta(seconds=1))
    over = record(tickets=(tk,), steps=(step(StepKind.NO_ACTION, 1, "unit.idle"),))
    v = run(t, obs(selector_present=True, currency=(short,)), over, now=5, host=CURRENT)
    assert v.condition is C.CONVERGING
    assert (
        run(t, obs(selector_present=True, currency=(short,)), over, now=11, host=CURRENT).code
        == TIMEOUT
    )
    t4 = terms(retryable=("tool.busy",), max_wait=10)
    na = record(
        tickets=(ticket(status=NOT_APPLIED, code="tool.busy"),),
        steps=(step(StepKind.NO_ACTION, 1, "unit.idle"),),
    )
    assert run(t4, obs(), na, now=2).condition is C.CONVERGING
    assert run(t4, obs(), na, now=11).condition is C.FAILED
    # group 1 precedes J-15: a Blocked step wins
    blocked = record(
        tickets=(tk,),
        steps=(step(StepKind.NO_ACTION, 1, "unit.idle"), step(StepKind.BLOCKED, 2, "unit.stuck")),
    )
    assert shape(run(t, obs(), blocked, now=3))[1:] == (C.BLOCKED, "unit.stuck")


def test_every_waiting_condition_has_elapsed_row() -> None:
    """V-3.5: every CONVERGING and IN_DOUBT row has a wait-elapsed row, and every STALE row a
    slice-ended row, so an OBSERVED node is never polled without bound. Swept over the ticket
    states x observation shapes x flags x NoAction-step grid, at a time far past both bounds."""
    waiting = {C.CONVERGING, C.IN_DOUBT, C.STALE}
    drift = fact(generation="g0")
    old = fact(older=codes.CREDENTIAL_STALE)
    observations = {
        "absent": obs(),
        "could-not-observe": obs(code="docker.cli_missing"),
        "selector": obs(selector_present=True),
        "selector-unready": obs(selector_present=True, post=False),
        "found": obs(found=1),
        "found-unready": obs(found=1, post=False),
        "found-drift": obs(found=1, currency=(drift,)),
        "selector-drift": obs(selector_present=True, currency=(drift,)),
        "selector-older": obs(selector_present=True, currency=(old,)),
        "found-older": obs(found=1, currency=(old,)),
    }
    ticket_states = {
        "none": (),
        "unconfirmed": (dict(status=None),),
        "unknown": (dict(status=UNKNOWN),),
        "confirmed-owned": (dict(status=APPLIED, with_handle=True),),
        "confirmed-found": (dict(status=APPLIED),),
    }
    seen_waiting: set[Condition] = set()
    checked = 0
    for repeat in (SAFE, ONCE):
        t = terms(repeat=repeat, max_wait=10, slice_end=100)
        for state, ticket_kwargs in ticket_states.items():
            for name, o in observations.items():
                for no_action in (False, True):
                    tickets = tuple(ticket(repeat=repeat, **kw) for kw in ticket_kwargs)
                    steps = (step(StepKind.NO_ACTION, 1, "unit.idle"),) if no_action else ()
                    rec = record(tickets=tickets, steps=steps)
                    early = run(t, o, rec, now=2, host=CURRENT)
                    seen_waiting |= {early.condition} & waiting
                    if early.condition in waiting:
                        commands = decide(t.flags, early.condition, Goal.CONVERGE)
                        assert Command.POLL in commands  # a waiting node is polled...
                    late = run(t, o, rec, now=10_000, host=CURRENT)
                    assert late.condition not in waiting, (repeat, state, name, no_action)
                    checked += 1
    assert checked == 2 * 5 * 10 * 2
    assert seen_waiting == waiting  # the sweep is not vacuous: each waiting condition occurs


def test_provenance_only_from_record_x_observation() -> None:
    t = terms()
    ok = obs(selector_present=True)
    # no ticket: observation only
    assert run(t, obs(), record()).provenance is P.ABSENT
    assert run(t, obs(found=1), record()).provenance is P.FOUND
    # a ticket issued, not confirmed: the record says CLAIMED; the observation adds CREATED
    assert run(t, obs(), record(tickets=(ticket(status=None),))).provenance is P.CLAIMED
    assert run(t, ok, record(tickets=(ticket(status=None),))).provenance is P.CREATED
    # confirmed: a CreatedHandle in the record is what makes it CREATED, not the observation
    assert (
        run(t, ok, record(tickets=(ticket(status=APPLIED, with_handle=True),))).provenance
        is P.CREATED
    )
    assert run(t, obs(found=1), record(tickets=(ticket(status=APPLIED),))).provenance is P.FOUND
    assert run(t, obs(), record(tickets=(ticket(status=APPLIED),))).provenance is P.ABSENT
    # a unit's steps never produce provenance or a satisfied node (V-3.3): the same record with
    # and without steps yields the verdict's provenance from the record x observation rows
    base = run(t, obs(found=1, post=False), record())
    with_step = run(
        t, obs(found=1, post=False), record(steps=(step(StepKind.FAILED, 1, "unit.broke"),))
    )
    assert base.provenance is with_step.provenance is P.FOUND
    assert with_step.condition is C.FAILED and base.condition is C.INCOMPATIBLE
    # the observation's payload and free-text detail are never read (V-3, V-3.2)
    noisy = replace(ok, payload={"provenance": "created", "owned": True})
    assert run(t, noisy, record()) == run(t, ok, record())
    # Observation carries no ownership or provenance field (V-3.2)
    names = {f.name for f in fields(Observation)}
    assert not {n for n in names if "provenance" in n or "owned" in n or "owner" in n}
    # an OBSERVED leaf's join needs its observation
    with pytest.raises(ValueError):
        run(t, None, record())
