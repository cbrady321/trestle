"""A one-vertex workflow whose only unit never returns from `observe` (L.SL-11.2): the fossil
producer of a timed-out plan-bearing root (`tests.proof.fossil_states.single`).

The unit is deliberately uncooperative: it blocks in `observe` until the process is killed, so the
run ends only by the deadline. The release point (deadline minus the release slice) writes the
stop row, the host's group stop ends the process tree, and the run is `timed_out` whatever the
unit's own wait would have allowed (a registrable leaf's wait always ends before the release
point, so a cooperative one-leaf root cannot reach it by waiting, L.SL-2.1). It declares no
effect, so nothing is created and nothing is left to release. (The leaf is a fossil producer's,
not a termination-swept input: its declaration constant is named `DECL` on purpose, because the
SA-09 scan of published leaf fixtures keys on the other spelling.)"""

from __future__ import annotations

import time
from datetime import timedelta
from typing import Any

from trestle.plugin import Context, trestle
from trestle.workflow import (
    CompletionSource,
    Compose,
    LeafDeclaration,
    LoopFlags,
    Repeat,
    WaitPolicy,
    WorkflowEntry,
)
from trestle.workflow.loop import run_tree
from trestle.workflow.units import (
    ActContext,
    EffectFacets,
    NoAction,
    ObserveContext,
    ReadFacets,
    Step,
)
from trestle.workflow.values import Observation, Verdict

UNIT = "hold_leaf"
HOLD_S = 3600  # far beyond any deadline the fixture is published with

DECL = LeafDeclaration(
    unit=UNIT,
    flags=LoopFlags(Compose.LEAF, CompletionSource.OBSERVED, Repeat.SAFE),
    preconditions=(),
    postcondition="ready",
    wait=WaitPolicy(timedelta(seconds=0.2), 1.0, timedelta(seconds=6)),
    resource_kind="marker",
    may_touch=frozenset({"marker"}),
    effects=(),
    retryable=frozenset(),
    remedies=(),
    budget=timedelta(seconds=8),
    max_attempts=2,
    env_key_field="env",
)


class HoldLeaf:
    def declare(self) -> LeafDeclaration:
        return DECL

    def observe(self, params: Any, reads: ReadFacets, ctx: ObserveContext) -> Observation:
        time.sleep(HOLD_S)  # uncooperative: ignores the stop; the host's group stop ends it
        raise RuntimeError("unreachable: the process is killed first")

    def advance(self, params: Any, state: Verdict, effects: EffectFacets, ctx: ActContext) -> Step:
        return NoAction("unit.nothing")

    def release(self, params: Any, handle: Any, effects: Any, ctx: ActContext) -> Step:
        return NoAction("unit.nothing")


ENTRY = WorkflowEntry(
    root=UNIT, units={UNIT: HoldLeaf()}, deadline=timedelta(seconds=24)
)  # budget 8 + release slice fit it


@trestle(deadline=24, env_arg="env")
def hold_leaf(ctx: Context, env: str = "dev") -> dict[str, str]:
    run_tree(ctx, ENTRY, {"env": env})
    return {"env": env}
