"""The one-vertex termination model (L.SV-5.17): join -> decide -> the seven-command table's
effect on a model record, under a model clock, against an adversary.

A model of the loop's step function over the pure modules that exist before the loop does
(`join`, `decide`); it does not drive `loop.py` (L.SV-5.13 does, over the same table). One leaf
vertex, one effect. The adversary chooses, at every observation, one of a fixed set of
observation shapes, and at every ADVANCE, what the unit does (issue its effect and have the port
confirm it any of the ways a port can, decline, block, fail, raise) and whether a stop flag
arrives before an iteration. The lane's admission rules are enforced by the model: `ONCE` refuses
a second ticket once one may have taken effect, and no ticket exceeds `max_attempts`.

Every reachable state is explored once. A path is unbounded when it revisits a state (no
progress: S-8's flip condition) or outruns `Params.bound`; an ADVANCE the lane would refuse is
recorded as a violation, because a refused advance records nothing and the next join is the
same. The exploration returns the longest path in loop iterations (one `decide` call each), the
most ADVANCEs and the most POLLs along any path.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta

from tests.single.workflow.joinkit import (
    APPLIED,
    NOT_APPLIED,
    ROOT_DEADLINE,
    UNKNOWN,
    clock,
    obs,
    readings,
    record,
    step,
    terms,
    ticket,
)
from trestle.workflow import codes
from trestle.workflow.decide import Command, NeverProduced, decide
from trestle.workflow.declarations import (
    CompletionSource,
    Compose,
    HostScopeRef,
    LoopFlags,
    Repeat,
)
from trestle.workflow.join import join
from trestle.workflow.values import (
    ConfirmationStatus,
    CurrencyFact,
    Goal,
    Observation,
    RecordedResult,
    StepKind,
)

RETRYABLE = "port.retryable"
FATAL = "port.fatal"

CURRENT = readings((HostScopeRef.DEMO_CREDENTIAL, "g1"))
_DRIFT = CurrencyFact(HostScopeRef.DEMO_CREDENTIAL, "g0", None)
_OLDER = CurrencyFact(HostScopeRef.DEMO_CREDENTIAL, "g1", None, older=codes.CREDENTIAL_STALE)
_SHORT = CurrencyFact(HostScopeRef.DEMO_CREDENTIAL, "g1", ROOT_DEADLINE - timedelta(seconds=1))

OBSERVED_SHAPES: dict[str, Observation] = {
    "absent": obs(),
    "absent-precondition-fails": obs(pre=(False,)),
    "could-not-observe": obs(code="docker.cli_missing"),
    "selector-ok": obs(selector_present=True),
    "selector-unready": obs(selector_present=True, post=False),
    "found-ok": obs(found=1),
    "found-unready": obs(found=1, post=False),
    "found-unproven": obs(found=1, identity=False),
    "found-drift": obs(found=1, currency=(_DRIFT,)),
    "selector-drift": obs(selector_present=True, currency=(_DRIFT,)),
    "selector-older": obs(selector_present=True, currency=(_OLDER,)),
    "found-older": obs(found=1, currency=(_OLDER,)),
    "selector-short-lifetime": obs(selector_present=True, currency=(_SHORT,)),
}
RECORDED_SHAPES: dict[str, Observation] = {
    "preconditions-hold": obs(pre=(True,)),
    "precondition-fails": obs(pre=(False,)),
}


class Unbounded(AssertionError):
    """A path with no progress or one past the bound: S-8's flip condition (escalate, never
    patch)."""


@dataclass(frozen=True)
class Params:
    completion: CompletionSource
    repeat: Repeat
    max_attempts: int = 2
    poll: int = 1
    max_wait: int = 2
    slice_end: int = 3
    release_timeout: int = 2

    @property
    def flags(self) -> LoopFlags:
        return LoopFlags(Compose.LEAF, self.completion, self.repeat)

    @property
    def bound(self) -> int:
        """Iterations, one `decide` call each: max_attempts x ceil(max_wait/poll) waiting polls
        (the plan's measure), plus the stale-slice polls, one ADVANCE-only iteration per attempt
        (a NOT_APPLIED retry advances and polls one interval), one NoAction wait, and the first
        and last joins. A RECORDED leaf never polls: it is bounded by its attempts alone."""
        if self.completion is CompletionSource.RECORDED:
            return self.max_attempts + 1
        waits = math.ceil(self.max_wait / self.poll)
        return (
            self.max_attempts * waits
            + math.ceil(self.slice_end / self.poll)
            + self.max_attempts
            + waits
            + 2
        )

    @property
    def release_cost(self) -> int:
        """Release-bound steps for one owned handle: the release call, then polls until observed
        absent or `release_timeout` (B1-C10 RELEASE_HANDLES)."""
        return 1 + math.ceil(self.release_timeout / self.poll)

    @property
    def shapes(self) -> dict[str, Observation]:
        return RECORDED_SHAPES if self.completion is CompletionSource.RECORDED else OBSERVED_SHAPES


# a ticket: (attempt, issued, status, code, has_handle, result); a step: (kind, at)
type Ticket = tuple[int, int, str | None, str | None, bool, str | None]
type StepRow = tuple[str, int]
type State = tuple[int, str, tuple[Ticket, ...], tuple[StepRow, ...], str]

_STOP = "STOP"
_RELEASED = "RELEASED"


@dataclass
class Report:
    iterations: int = 0  # decide calls on the longest path, the release walk counted as one
    advances: int = 0
    polls: int = 0
    release_steps: int = 0  # the longest path's release-bound steps (owned handles x release_cost)
    total: int = 0  # the longest path's iterations plus its release-bound steps
    states: int = 0
    violations: list[str] = field(default_factory=list)
    conditions: set[str] = field(default_factory=set)


DecideFn = Callable[[LoopFlags, object, Goal], tuple[Command, ...]]


def _record(params: Params, state: State):  # noqa: ANN202
    _, _, tickets, steps, _ = state
    results = {
        "passed": RecordedResult(True, None, None),
        "failed": RecordedResult(False, "t", None),
    }
    return record(
        tickets=tuple(
            ticket(
                attempt=a,
                issued=at,
                status=None if status is None else ConfirmationStatus(status),
                code=code,
                with_handle=handle,
                result=results[result] if result else None,
                repeat=params.repeat,
            )
            for a, at, status, code, handle, result in tickets
        ),
        steps=tuple(step(StepKind(kind), when, "unit.said") for kind, when in steps),
    )


def _lane_refusal(params: Params, tickets: tuple[Ticket, ...]) -> str | None:
    """Admission the lane applies to a new ticket (V-4.7): ONCE once one may have taken effect,
    and never past max_attempts."""
    if params.repeat is Repeat.ONCE and any(t[2] != NOT_APPLIED.value for t in tickets):
        return "once_already_issued"
    if len(tickets) + 1 > params.max_attempts:
        return "attempts_spent"
    return None


def _outcomes(params: Params) -> list[tuple[str, str | None, bool, str | None]]:
    """What the port can confirm: (status, code, handle, result)."""
    base: list[tuple[str, str | None, bool, str | None]] = [
        (APPLIED.value, None, True, None),
        (APPLIED.value, None, False, None),
        (NOT_APPLIED.value, RETRYABLE, False, None),
        (NOT_APPLIED.value, FATAL, False, None),
        (NOT_APPLIED.value, codes.CREDENTIAL_INTERACTIVE, False, None),
        (UNKNOWN.value, None, False, None),
    ]
    if params.completion is CompletionSource.RECORDED:
        base += [
            (APPLIED.value, None, False, "passed"),
            (APPLIED.value, None, False, "failed"),
        ]
    return base


def explore(
    params: Params,
    *,
    decide_fn: DecideFn = decide,
    join_fn: Callable[..., object] = join,
) -> Report:
    t = terms(
        completion=params.completion,
        repeat=params.repeat,
        retryable=(RETRYABLE,),
        poll=params.poll,
        max_wait=params.max_wait,
        max_attempts=params.max_attempts,
        slice_end=params.slice_end,
    )
    shapes = params.shapes
    report = Report()
    memo: dict[State, tuple[int, int, int, int]] = {}
    on_stack: set[State] = set()
    cap = params.bound * 2 + 10

    def owned(state: State) -> int:
        return sum(1 for tk in state[2] if tk[4])

    def apply(cmd: Command, state: State) -> list[State | str]:
        now, goal, tickets, steps, _ = state
        if cmd is Command.STOP:
            return [_STOP]
        if cmd is Command.RELEASE_HANDLES:
            return [_RELEASED]
        if cmd is Command.REJOIN:
            return [state]
        if cmd is Command.POLL:
            return [(now + params.poll, goal, tickets, steps, name) for name in shapes]
        if cmd is Command.ADVANCE:
            outs: list[State | str] = []
            refusal = _lane_refusal(params, tickets)
            if refusal is None:
                for status, code, handle, result in _outcomes(params):
                    ticket_row = (len(tickets) + 1, now, status, code, handle, result)
                    outs.append((now, goal, (*tickets, ticket_row), steps, state[4]))
            else:  # the unit asks, the lane refuses, nothing is recorded
                report.violations.append(f"advance refused ({refusal}) at {state}")
                outs.append(state)
            if params.completion is CompletionSource.OBSERVED:
                outs.append(
                    (now, goal, tickets, (*steps, (StepKind.NO_ACTION.value, now)), state[4])
                )
            for kind in (StepKind.BLOCKED, StepKind.FAILED):
                outs.append((now, goal, tickets, (*steps, (kind.value, now)), state[4]))
            outs.append((now, Goal.RELEASE.value, tickets, steps, state[4]))  # UNIT_RAISED flip
            return outs
        raise NotImplementedError(f"{cmd} is not a leaf command")

    def successors(state: State) -> list[State | str]:
        now, goal, _, _, obs_name = state
        if goal == Goal.RELEASE.value:
            return [_RELEASED]
        nexts: list[State | str] = []
        # a stop flag may arrive before any iteration: the goal flips to RELEASE
        nexts.append((now, Goal.RELEASE.value, state[2], state[3], obs_name))
        verdict = join_fn(t, shapes[obs_name], _record(params, state), CURRENT, clock(now))
        report.conditions.add(verdict.condition.value)  # type: ignore[attr-defined]
        try:
            commands = decide_fn(params.flags, verdict.condition, Goal.CONVERGE)  # type: ignore[attr-defined]
        except NeverProduced as exc:
            report.violations.append(f"never-produced combination reached: {exc} at {state}")
            return nexts
        frontier: list[State | str] = [state]
        for cmd in commands:
            step_out: list[State | str] = []
            for s in frontier:
                step_out.extend([s] if isinstance(s, str) else apply(cmd, s))
            frontier = step_out
        return nexts + frontier

    def visit(state: State, depth: int) -> tuple[int, int, int, int]:
        if state in memo:
            return memo[state]
        if state in on_stack:
            raise Unbounded(f"no progress: state revisited {state}")
        if depth > cap:
            raise Unbounded(f"path longer than {cap} iterations at {state}")
        on_stack.add(state)
        best = (0, 0, 0, 0)  # iterations, advances, polls, iterations + release steps
        for nxt in successors(state):
            if nxt == _STOP:
                cost = (1, 0, 0, 1)
            elif nxt == _RELEASED:
                cost = (1, 0, 0, 1 + owned(state) * params.release_cost)
            else:
                assert not isinstance(nxt, str)
                inner = visit(nxt, depth + 1)
                # one iteration per decide call; a stop flag flipping the goal costs none
                flip = nxt[1] == Goal.RELEASE.value and state[1] == Goal.CONVERGE.value
                advanced = len(nxt[2]) + len(nxt[3]) - len(state[2]) - len(state[3])
                step_cost = 0 if flip else 1
                cost = (
                    inner[0] + step_cost,
                    inner[1] + (1 if advanced > 0 else 0),
                    inner[2] + (1 if nxt[0] > state[0] else 0),
                    inner[3] + step_cost,
                )
            best = tuple(max(a, b) for a, b in zip(best, cost, strict=True))  # type: ignore[assignment]
        on_stack.discard(state)
        memo[state] = best
        return best

    worst = (0, 0, 0, 0)
    for name in shapes:
        worst_i = visit((0, Goal.CONVERGE.value, (), (), name), 0)
        worst = tuple(max(a, b) for a, b in zip(worst, worst_i, strict=True))  # type: ignore[assignment]
    report.iterations, report.advances, report.polls, report.total = worst
    report.release_steps = report.total - report.iterations
    report.states = len(memo)
    return report
