"""A unit blocked in `wait_for` wakes when a sibling raises (DEFERRED-DECISIONS Q1; not fossilized).

`app` runs `raiser` and `waiter` at once. `waiter` creates its marker, then, at its next
observation, waits through `ctx.cancellation.wait_for` on a condition that never holds, bounded
well past the run; it records how the wait ended (`wait.ended`). `raiser` waits until `waiter` is
in that wait, then raises from `advance`. The raise flips the root's goal to RELEASE, which wakes
the waiting unit: its wait returns `interrupted` at once, it records nothing further but its
release, and it ends `cut=STOPPED`. Three vertices, depth 2."""

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

LABEL = {"vertices": 3, "depth": 2, "shared": None, "expect": "valid"}

CREATE_EFFECT = "up"
STOP_EFFECT = "stop"

LEAF_BUDGET_S = 10
ROOT_BUDGET_S = 20
DEADLINE_S = 36
WAIT_S = 8  # the waiter's bound: past anything the raise should take to wake it, under its slice
PAUSE_BOUND_S = 6  # the raiser's bound on waiting for the waiter to be in its wait

WAITING = threading.Event()  # set by the waiter just before it enters `wait_for`


class Node:
    """`raiser` waits for the waiter, then raises from `advance`; `waiter` creates its marker and
    then waits on a condition that never holds."""

    def __init__(self, unit: str, *, raises: bool) -> None:
        self._unit = unit
        self._raises = raises
        self._created = False
        self._waited = False
        self._spec = ResourceSpec(
            unit, RealizationKind.AGENT_LAUNCHED_PROJECT, "wait-interface", None
        )

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
        if self._raises:
            ctx.cancellation.wait_for(WAITING.is_set, timedelta(seconds=PAUSE_BOUND_S))
        elif self._created and not self._waited:
            self._waited = True
            WAITING.set()
            outcome = ctx.cancellation.wait_for(lambda: False, timedelta(seconds=WAIT_S))
            ctx.evidence.event("wait.ended", {"why": outcome.value})
        resource = reads.read(ResourceReads)
        seen = resource.observe(self._spec, ctx.lineage, CREATE_EFFECT)
        return Observation(
            present=seen.selector_present or bool(seen.found),
            selector_present=seen.selector_present,
            identity_proven=seen.identity_proven,
            configuration_compatible=seen.configuration_compatible,
            postcondition=CheckResult(False, None, ""),
            preconditions=(),
            currency=(),
            found=tuple(FoundRef(f.resource_kind, f.selector, f.observed_at) for f in seen.found),
            code=seen.code,
            payload=None,
        )

    def advance(self, params: Any, state: Verdict, effects: EffectFacets, ctx: ActContext) -> Step:
        if self._raises:
            raise RuntimeError("fixture: the unit raised")
        effects.create(ResourceCreate).create(self._spec, CREATE_EFFECT)
        self._created = True
        return Acted()

    def release(self, params: Any, handle: CreatedHandle, effects: Any, ctx: ActContext) -> Step:
        effects.owned(ResourceOwned).stop(handle, STOP_EFFECT)
        return Acted()


ENTRY = WorkflowEntry(
    root="app",
    units={
        "app": AllDeclaration(
            unit="app",
            flags=LoopFlags(Compose.ALL, CompletionSource.OBSERVED, Repeat.SAFE),
            children=(
                ChildBinding(unit="raiser", params={}, needs=()),
                ChildBinding(unit="waiter", params={}, needs=()),
            ),
            concurrency=2,
            budget=timedelta(seconds=ROOT_BUDGET_S),
            identifier_sets={},
            arg_bindings=(),
            env_key_field="env",
        ),
        "raiser": Node("raiser", raises=True),
        "waiter": Node("waiter", raises=False),
    },
    deadline=timedelta(seconds=DEADLINE_S),
)


@trestle(deadline=36, env_arg="env")  # deadline: DEADLINE_S (a decorator argument is a literal)
def wait_interface(ctx: Context, env: str = "dev") -> dict[str, str]:
    marker = FakeMarker(ctx.tmp / "markers", "run")
    ports = {ResourceReads: marker, ResourceCreate: marker, ResourceOwned: marker}
    run_tree(ctx, ENTRY, {"env": env}, ports=ports)
    return {"env": env}
