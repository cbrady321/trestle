"""The loop's one per-node branch: `decide` over B1-C10's normative table (MC-24; L.SV-5.3).

Pure, no I/O, stdlib and this package only. The loop reads `LoopFlags`, the join's `Condition` and
the goal here and nowhere else (V-6.1); it never compares a resource kind, a realization kind, a
port kind or a lifetime. The tuple is the table's command sequence, in order; after the last
command the loop joins again and calls `decide` with the new condition (B1-C10 "Tuples run in
order").
"""

from __future__ import annotations

from enum import StrEnum

from trestle.workflow.declarations import CompletionSource, Compose, LoopFlags, Repeat
from trestle.workflow.values import Condition, Goal


class Command(StrEnum):
    WALK = "walk"  # children, by needs, under concurrency; descending plan release rank on RELEASE
    SELECT = "select"  # V-7.2, then WALK the selected alternative
    ADVANCE = "advance"
    POLL = "poll"  # wait the policy's next interval, observe, join again; never re-invoke to wait
    REJOIN = "rejoin"  # join again from the record without observing
    RELEASE_HANDLES = "release"
    STOP = "stop"


class NeverProduced(ValueError):
    """A (flags, condition, goal) the join cannot produce (B1-C10 "Combinations the join never
    produces"). Reaching one is a join defect: it is raised, never answered with a guess."""


_TERMINAL = frozenset(
    {Condition.SATISFIED, Condition.BLOCKED, Condition.FAILED, Condition.INCOMPATIBLE}
)


def decide(flags: LoopFlags, condition: Condition, goal: Goal) -> tuple[Command, ...]:
    if goal is Goal.RELEASE:
        if flags.compose is Compose.LEAF:
            return (Command.RELEASE_HANDLES,)
        return (Command.WALK,)
    # goal is CONVERGE
    if flags.compose is Compose.ALL:
        return (Command.WALK,)
    if flags.compose is Compose.CHOICE:
        return (Command.SELECT, Command.WALK)
    # a LEAF under CONVERGE
    observed = flags.completion is CompletionSource.OBSERVED
    once = flags.repeat is Repeat.ONCE
    if condition in _TERMINAL:
        return (Command.STOP,)
    if condition is Condition.STALE:
        return (Command.POLL,)
    if condition is Condition.UNSATISFIED:
        if observed:
            return (Command.ADVANCE, Command.POLL)
        return (Command.ADVANCE, Command.REJOIN)
    if condition is Condition.CONVERGING:
        if observed:
            return (Command.POLL,)
        raise NeverProduced("RECORDED x CONVERGING: J-6..J-9 have no such row")
    # IN_DOUBT
    if once:
        return (Command.POLL,) if observed else (Command.STOP,)
    raise NeverProduced("SAFE x IN_DOUBT: J-7, J-17 give UNSATISFIED or CONVERGING")
