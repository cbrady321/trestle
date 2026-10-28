"""L.SV-5.17: the early termination model over decide + join (S-8's flip condition, before the
loop exists). Runs in the lane and again at J-SV5 beside the real `loop.run_tree` (L.SV-5.13)."""

from __future__ import annotations

import math
from dataclasses import replace

import pytest

from tests.single.workflow.termination_model import Params, Unbounded, explore
from trestle.workflow.decide import Command, decide
from trestle.workflow.declarations import CompletionSource, LoopFlags, Repeat
from trestle.workflow.join import join
from trestle.workflow.values import Condition, Goal, Verdict

OBSERVED, RECORDED = CompletionSource.OBSERVED, CompletionSource.RECORDED

PARAM_SETS = [
    (OBSERVED, SAFE := Repeat.SAFE, {}),
    (OBSERVED, ONCE := Repeat.ONCE, {}),
    (OBSERVED, SAFE, {"max_attempts": 3, "poll": 2, "max_wait": 3, "slice_end": 4}),
    (OBSERVED, ONCE, {"max_attempts": 1, "poll": 1, "max_wait": 1, "slice_end": 1}),
    (RECORDED, SAFE, {}),
    (RECORDED, ONCE, {}),
    (RECORDED, SAFE, {"max_attempts": 3}),
]


@pytest.mark.parametrize(("completion", "repeat", "overrides"), PARAM_SETS)
def test_every_one_vertex_path_terminates_within_bound(
    completion: CompletionSource, repeat: Repeat, overrides: dict[str, int]
) -> None:
    params = Params(completion, repeat, **overrides)
    report = explore(params)
    assert report.violations == []  # no ADVANCE the lane must refuse, no never-produced row
    assert report.iterations <= params.bound, (report, params.bound)
    # the release walk adds its own bounded steps: per owned handle, the release call and the
    # polls until observed absent or release_timeout (B1-C10 RELEASE_HANDLES)
    assert report.total <= params.bound + params.max_attempts * params.release_cost
    if completion is OBSERVED:
        waits = math.ceil(params.max_wait / params.poll)
        # the plan's measure: max_attempts x ceil(max_wait/poll_every) waiting polls, plus the
        # polls a STALE node spends until its slice ends
        assert report.polls <= params.max_attempts * waits + math.ceil(
            params.slice_end / params.poll
        )
    # the sweep is not vacuous: real paths of every length class were explored
    assert report.states > 10
    assert report.advances >= 1 and report.iterations >= 3
    if completion is OBSERVED and repeat is SAFE and not overrides:
        assert report.polls >= params.max_attempts * math.ceil(params.max_wait / params.poll)
        assert {c.value for c in Condition} - {"unsatisfied"} <= report.conditions | {"in_doubt"}, (
            report.conditions
        )


@pytest.mark.parametrize("repeat", [SAFE, ONCE])
@pytest.mark.parametrize("attempts", [1, 2, 3])
def test_recorded_advance_rejoin_bounded_by_max_attempts(repeat: Repeat, attempts: int) -> None:
    """A RECORDED leaf is never polled: ADVANCE then REJOIN, and only while a ticket is issuable,
    so its loop is bounded by max_attempts alone (J-7, J-7a, J-8, J-4)."""
    params = Params(RECORDED, repeat, max_attempts=attempts)
    report = explore(params)
    assert report.violations == []
    assert report.polls == 0
    assert report.advances <= attempts
    assert report.iterations <= attempts + 1
    assert report.advances >= 1
    # and the table says so: a RECORDED leaf's UNSATISFIED is ADVANCE, REJOIN, never POLL
    flags = params.flags
    assert decide(flags, Condition.UNSATISFIED, Goal.CONVERGE) == (
        Command.ADVANCE,
        Command.REJOIN,
    )


def test_planted_unbounded_readvance_caught() -> None:
    """S-8's flip condition: a table row whose ADVANCE spends no attempt is reported unbounded."""

    def planted(flags: LoopFlags, condition: Condition, goal: Goal) -> tuple[Command, ...]:
        if goal is Goal.CONVERGE and condition is Condition.CONVERGING:
            return (Command.ADVANCE, Command.REJOIN)  # re-advances a node that is only waiting
        return decide(flags, condition, goal)

    params = Params(OBSERVED, SAFE)
    with pytest.raises(Unbounded):
        explore(params, decide_fn=planted)

    # a join that lets a node re-advance after its attempts are spent (here: FAILED is downgraded
    # to UNSATISFIED) makes the lane refuse every advance; the refused advance records nothing,
    # so the model never reaches a STOP: it is reported unbounded, not silently accepted
    def lenient_join(*args, **kwargs) -> Verdict:  # noqa: ANN002, ANN003
        verdict = join(*args, **kwargs)
        if verdict.condition is Condition.FAILED:
            return replace(verdict, condition=Condition.UNSATISFIED, code=None)
        return verdict

    with pytest.raises(Unbounded):
        explore(Params(OBSERVED, ONCE), join_fn=lenient_join)

    # the unplanted table and join are bounded over the same inputs (the plant is what changed)
    assert explore(params).iterations <= params.bound
