"""A failing node with two dependents and an independent sibling (L.TR-3.5; MC-B3-01 behaviour
fixture; consumed by L.TR-4.6, L.TR-L.9 and L.TR-L.10).

`app` runs `broken`, `left` and `right` (each needs `broken`) and `independent`. The `mode` argument
says how `broken` ends: `failed` (its `advance` returns `Failed`) or `blocked` (it returns `Blocked`
with a human action). Either way it is an ordinary failure, not an exception: only its dependents
are cut (OQ-33), never written started, while `independent` runs to its own terminal condition.
Five vertices, depth 2."""

from __future__ import annotations

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
from trestle.workflow.units import (
    ActContext,
    Acted,
    Blocked,
    EffectFacets,
    Failed,
    ObserveContext,
    ReadFacets,
    Step,
)
from trestle.workflow.values import (
    CheckResult,
    CreatedHandle,
    FoundRef,
    Observation,
    Resend,
    Verdict,
)

# MC-B3-01. `vertices` counts the distinct logical nodes the declaration references; `depth` is
# the longest containment chain, a leaf being depth 1; `shared` names the node two parents
# reference, or None; `expect` is "valid" or the ground the tree is defective in.
LABEL = {"vertices": 5, "depth": 2, "shared": None, "expect": "valid"}

CREATE_EFFECT = "up"
STOP_EFFECT = "stop"

LEAF_BUDGET_S = 10
ROOT_BUDGET_S = 40
DEADLINE_S = 60


class Node:
    """One leaf. `role` is `broken` (ends as `mode` says without acting), or `works` (observe the
    node's marker, create it once, release it on the way out)."""

    def __init__(self, unit: str, role: str) -> None:
        self._unit = unit
        self._role = role
        # one logical system per node: a marker another node made is not this node's to find
        self._spec = ResourceSpec(
            unit, RealizationKind.AGENT_LAUNCHED_PROJECT, "failure-dependents", None
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
        if self._role == "broken":
            if params.get("mode") == "blocked":
                return Blocked(
                    "fixture.blocked", "Fix the fixture, then re-send.", Resend.WILL_NOT_SUCCEED
                )
            return Failed("fixture.broke", "the fixture failed on request")
        effects.create(ResourceCreate).create(self._spec, CREATE_EFFECT)
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
                ChildBinding(unit="broken", params={"mode": "mode"}, needs=()),
                ChildBinding(unit="left", params={}, needs=("broken",)),
                ChildBinding(unit="right", params={}, needs=("broken",)),
                ChildBinding(unit="independent", params={}, needs=()),
            ),
            concurrency=3,
            budget=timedelta(seconds=ROOT_BUDGET_S),
            identifier_sets={},
            arg_bindings=(),
            env_key_field="env",
        ),
        "broken": Node("broken", "broken"),
        "left": Node("left", "works"),
        "right": Node("right", "works"),
        "independent": Node("independent", "works"),
    },
    deadline=timedelta(seconds=DEADLINE_S),
)


@trestle(deadline=60, env_arg="env")  # deadline: DEADLINE_S (a decorator argument is a literal)
def failure_dependents(ctx: Context, env: str = "dev", mode: str = "failed") -> dict[str, str]:
    marker = FakeMarker(ctx.tmp / "markers", "run")
    ports = {ResourceReads: marker, ResourceCreate: marker, ResourceOwned: marker}
    run_tree(ctx, ENTRY, {"env": env, "mode": mode}, ports=ports)
    return {"env": env, "mode": mode}
