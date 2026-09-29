"""L.SV-5.4 / L.SV-5.5: the pure join over V-3.1's rows in V-3.6's group order."""

from __future__ import annotations

from tests.single.workflow.joinkit import (
    APPLIED,
    NO_READINGS,
    NOT_APPLIED,
    ONCE,
    RECORDED,
    UNCONFIRMED,
    UNKNOWN,
    clock,
    conf,
    handle,
    obs,
    record,
    step,
    terms,
    ticket,
)
from trestle.workflow import codes
from trestle.workflow.join import join
from trestle.workflow.values import (
    Condition,
    Provenance,
    RecordedResult,
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
