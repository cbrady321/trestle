"""One child in a readiness wait beside a running sibling branch (L.TR-3.6; MC-B3-01 behaviour
fixture; consumed by L.TR-L.8 and L.TR-L.9).

`app` runs `waiter`, a leaf that creates its marker and then polls for a readiness that never comes,
and `branch`, a group of two such leaves (`w1`, `w2`): so a cancel or a deadline finds one child in
a readiness wait while a sibling branch is running. Every node has created and is polling, so a stop
must leave no applied non-release entry past its offset in any path. Five vertices, depth 3."""

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
LABEL = {"vertices": 5, "depth": 3, "shared": None, "expect": "valid"}

CREATE_EFFECT = "up"
STOP_EFFECT = "stop"

# The tree's worst case (branch 44 s + reserve 10 s in the root) plus the release slice (10 s) is
# 64 s; the deadline leaves 6 s over it so the dispatch check passes on a real run.
LEAF_BUDGET_S = 34
BRANCH_BUDGET_S = 44
ROOT_BUDGET_S = 54
DEADLINE_S = 70


class Waiter:
    """Creates its marker once and polls for a postcondition that never holds."""

    def __init__(self, unit: str) -> None:
        self._unit = unit
        # one logical system per node: a marker another node made is not this node's to find
        self._spec = ResourceSpec(
            unit, RealizationKind.AGENT_LAUNCHED_PROJECT, "readiness-sibling", None
        )

    def declare(self) -> LeafDeclaration:
        return LeafDeclaration(
            unit=self._unit,
            flags=LoopFlags(Compose.LEAF, CompletionSource.OBSERVED, Repeat.SAFE),
            preconditions=(),
            postcondition="ready",
            wait=WaitPolicy(timedelta(seconds=1), 1.0, timedelta(seconds=30)),
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
        effects.create(ResourceCreate).create(self._spec, CREATE_EFFECT)
        return Acted()

    def release(self, params: Any, handle: CreatedHandle, effects: Any, ctx: ActContext) -> Step:
        effects.owned(ResourceOwned).stop(handle, STOP_EFFECT)
        return Acted()


def _group(
    unit: str, children: tuple[ChildBinding, ...], budget_s: int, concurrency: int
) -> AllDeclaration:
    return AllDeclaration(
        unit=unit,
        flags=LoopFlags(Compose.ALL, CompletionSource.OBSERVED, Repeat.SAFE),
        children=children,
        concurrency=concurrency,
        budget=timedelta(seconds=budget_s),
        identifier_sets={},
        arg_bindings=(),
        env_key_field="env" if unit == "app" else None,
    )


ENTRY = WorkflowEntry(
    root="app",
    units={
        "app": _group(
            "app",
            (
                ChildBinding(unit="waiter", params={}, needs=()),
                ChildBinding(unit="branch", params={}, needs=()),
            ),
            ROOT_BUDGET_S,
            3,  # the waiter and both leaves of the branch run at once
        ),
        "branch": _group(
            "branch",
            (
                ChildBinding(unit="w1", params={}, needs=()),
                ChildBinding(unit="w2", params={}, needs=()),
            ),
            BRANCH_BUDGET_S,
            2,
        ),
        "waiter": Waiter("waiter"),
        "w1": Waiter("w1"),
        "w2": Waiter("w2"),
    },
    deadline=timedelta(seconds=DEADLINE_S),
)


@trestle(deadline=70, env_arg="env")  # deadline: DEADLINE_S (a decorator argument is a literal)
def readiness_sibling(ctx: Context, env: str = "dev") -> dict[str, str]:
    marker = FakeMarker(ctx.tmp / "markers", "run")
    ports = {ResourceReads: marker, ResourceCreate: marker, ResourceOwned: marker}
    run_tree(ctx, ENTRY, {"env": env}, ports=ports)
    return {"env": env}
