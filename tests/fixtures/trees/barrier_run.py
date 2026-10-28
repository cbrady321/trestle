"""Four independent leaves under a bound of two, then a barrier node (L.TR-L.2; MC-B3-01 behaviour
fixture; consumed by L.TR-L.2's host re-run of L.TR-3.2).

`barrier` runs `a`, `b`, `c` and `d` at a declared concurrency of two; `join` needs all four.
Each of the four waits in its create for a partner (a two-party barrier that trips for every pair),
so a walk that ran fewer than two at once breaks the meeting and a walk that ran more than two shows
in the lane. A leaf lingers a moment after it created its marker, so a `join` started before every
branch was terminal would show against an open branch. Six vertices, depth 2."""

from __future__ import annotations

import threading
from datetime import timedelta
from typing import Any

from trestle_packs.fakes import FakeMarker

from trestle.plugin import Context, trestle
from trestle.workflow import (
    AllDeclaration,
    ChildBinding,
    CompletionSource,
    Compose,
    EffectDeclaration,
    EffectFacetClass,
    LeafDeclaration,
    Lifetime,
    LoopFlags,
    RealizationKind,
    Repeat,
    WaitPolicy,
    WorkflowEntry,
)
from trestle.workflow.loop import run_tree
from trestle.workflow.ports import ResourceCreate, ResourceOwned, ResourceReads, ResourceSpec
from trestle.workflow.units import ActContext, Acted, EffectFacets, ObserveContext, ReadFacets, Step
from trestle.workflow.values import (
    CheckResult,
    CreatedHandle,
    FoundRef,
    Observation,
    Verdict,
)

# MC-B3-01. `vertices` counts the distinct logical nodes the declaration references; `depth` is
# the longest containment chain, a leaf being depth 1; `shared` names the node two parents
# reference, or None; `expect` is "valid" or the ground the tree is defective in.
LABEL = {"vertices": 6, "depth": 2, "shared": None, "expect": "valid"}

CREATE_EFFECT = "up"
STOP_EFFECT = "stop"

BOUND = 2  # the declared concurrency, and the party count of the meeting
MEET_S = 8  # a partner that never comes breaks the meeting (the unit then raises)
LINGER_S = 1  # a branch stays open this long (cancellably) after it created its marker
LEAF_BUDGET_S = 15
ROOT_BUDGET_S = 60
DEADLINE_S = 90
BRANCHES = ("a", "b", "c", "d")

_MEETING = threading.Barrier(BOUND)


class Node:
    """A branch (meets a partner, creates its marker, lingers) or the `join` (creates its marker)"""

    def __init__(self, unit: str, *, branch: bool) -> None:
        self._unit = unit
        self._branch = branch
        # one logical system per node: a marker another node made is not this node's to find
        self._spec = ResourceSpec(unit, RealizationKind.AGENT_LAUNCHED_PROJECT, "barrier-run", None)

    def declare(self) -> LeafDeclaration:
        return LeafDeclaration(
            unit=self._unit,
            flags=LoopFlags(Compose.LEAF, CompletionSource.OBSERVED, Repeat.SAFE),
            preconditions=(),
            postcondition="ready",
            wait=WaitPolicy(timedelta(seconds=1), 1.0, timedelta(seconds=6)),
            resource_kind="marker",
            may_touch=frozenset({"marker"}),
            effects=(
                EffectDeclaration(
                    CREATE_EFFECT,
                    EffectFacetClass.CREATE,
                    "",
                    Lifetime.RUN,
                    frozenset(),
                    timedelta(seconds=2),
                ),
                EffectDeclaration(
                    STOP_EFFECT,
                    EffectFacetClass.OWNED,
                    "",
                    Lifetime.RUN,
                    frozenset(),
                    timedelta(seconds=2),
                    is_release=True,
                ),
            ),
            retryable=frozenset(),
            remedies=(),
            budget=timedelta(seconds=LEAF_BUDGET_S),
            max_attempts=1,
        )

    def observe(self, params: Any, reads: ReadFacets, ctx: ObserveContext) -> Observation:
        resource = reads.read(ResourceReads)
        seen = resource.observe(self._spec, ctx.lineage, CREATE_EFFECT)
        checked = resource.check("ready", seen.selector_ref) if seen.selector_ref else None
        ready = checked is not None and checked.satisfied
        return Observation(
            present=seen.selector_present or bool(seen.found),
            selector_present=seen.selector_present,
            identity_proven=seen.identity_proven,
            configuration_compatible=seen.configuration_compatible,
            postcondition=CheckResult(ready, None, ""),
            preconditions=(),
            currency=(),
            found=tuple(FoundRef(f.resource_kind, f.selector, f.observed_at) for f in seen.found),
            code=seen.code,
            payload=None,
        )

    def advance(self, params: Any, state: Verdict, effects: EffectFacets, ctx: ActContext) -> Step:
        if self._branch:
            _MEETING.wait(timeout=MEET_S)
        effects.create(ResourceCreate).create(self._spec, CREATE_EFFECT)
        if self._branch:
            ctx.cancellation.wait(timedelta(seconds=LINGER_S))
        return Acted()

    def release(self, params: Any, handle: CreatedHandle, effects: Any, ctx: ActContext) -> Step:
        effects.owned(ResourceOwned).stop(handle, STOP_EFFECT)
        return Acted()


ENTRY = WorkflowEntry(
    root="barrier",
    units={
        "barrier": AllDeclaration(
            unit="barrier",
            flags=LoopFlags(Compose.ALL, CompletionSource.OBSERVED, Repeat.SAFE),
            children=(
                *(ChildBinding(unit=name, params={}, needs=()) for name in BRANCHES),
                ChildBinding(unit="join", params={}, needs=BRANCHES),
            ),
            concurrency=BOUND,
            budget=timedelta(seconds=ROOT_BUDGET_S),
            identifier_sets={},
            arg_bindings=(),
            env_key_field="env",
        ),
        **{name: Node(name, branch=True) for name in BRANCHES},
        "join": Node("join", branch=False),
    },
    deadline=timedelta(seconds=DEADLINE_S),
)


@trestle(deadline=90, env_arg="env")  # deadline: DEADLINE_S (a decorator argument is a literal)
def barrier_run(ctx: Context, env: str = "dev") -> dict[str, str]:
    marker = FakeMarker(ctx.tmp / "markers", "run")
    ports = {ResourceReads: marker, ResourceCreate: marker, ResourceOwned: marker}
    run_tree(ctx, ENTRY, {"env": env}, ports=ports)
    return {"fixture": "barrier_run", "env": env}
