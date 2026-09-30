"""A child precondition an upstream node covers (L.TR-L.2; MC-B3-01 behaviour fixture; consumed by
L.TR-L.2's host re-run of L.TR-3.2's upstream-covered case).

`consumer` declares the precondition `producer_ready`, which `producer` establishes, and `needs` the
producer, so the tree is valid (the structural `uncovered_precondition` is the same tree without the
`needs`). The producer lingers (cancellably) before it creates its marker, and the consumer reports
the precondition satisfied only once the producer's create was applied, so a consumer started
early would be blocked, never satisfied. Three vertices, depth 2."""

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
LABEL = {"vertices": 3, "depth": 2, "shared": None, "expect": "valid"}

CREATE_EFFECT = "up"
STOP_EFFECT = "stop"

LINGER_S = 1  # the producer stays open this long (cancellably) before it acts
LEAF_BUDGET_S = 15
ROOT_BUDGET_S = 40
DEADLINE_S = 60

_PRODUCED = threading.Event()  # set once the producer's create was applied


class Node:
    """The `producer` (lingers, creates its marker) or the `consumer` (whose precondition holds
    once the producer produced)."""

    def __init__(self, unit: str, *, consumer: bool) -> None:
        self._unit = unit
        self._consumer = consumer
        self._spec = ResourceSpec(
            unit, RealizationKind.AGENT_LAUNCHED_PROJECT, "upstream-covered", None
        )

    def declare(self) -> LeafDeclaration:
        return LeafDeclaration(
            unit=self._unit,
            flags=LoopFlags(Compose.LEAF, CompletionSource.OBSERVED, Repeat.SAFE),
            preconditions=("producer_ready",) if self._consumer else (),
            postcondition="ready" if self._consumer else "producer_ready",
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
        pre = (
            (("producer_ready", CheckResult(_PRODUCED.is_set(), None, "")),)
            if self._consumer
            else ()
        )
        return Observation(
            present=seen.selector_present or bool(seen.found),
            selector_present=seen.selector_present,
            identity_proven=seen.identity_proven,
            configuration_compatible=seen.configuration_compatible,
            postcondition=CheckResult(ready, None, ""),
            preconditions=pre,
            currency=(),
            found=tuple(FoundRef(f.resource_kind, f.selector, f.observed_at) for f in seen.found),
            code=seen.code,
            payload=None,
        )

    def advance(self, params: Any, state: Verdict, effects: EffectFacets, ctx: ActContext) -> Step:
        if not self._consumer:
            ctx.cancellation.wait(timedelta(seconds=LINGER_S))
        effects.create(ResourceCreate).create(self._spec, CREATE_EFFECT)
        if not self._consumer:
            _PRODUCED.set()
        return Acted()

    def release(self, params: Any, handle: CreatedHandle, effects: Any, ctx: ActContext) -> Step:
        effects.owned(ResourceOwned).stop(handle, STOP_EFFECT)
        return Acted()


ENTRY = WorkflowEntry(
    root="covered",
    units={
        "covered": AllDeclaration(
            unit="covered",
            flags=LoopFlags(Compose.ALL, CompletionSource.OBSERVED, Repeat.SAFE),
            children=(
                ChildBinding(unit="producer", params={}, needs=()),
                ChildBinding(unit="consumer", params={}, needs=("producer",)),
            ),
            concurrency=2,
            budget=timedelta(seconds=ROOT_BUDGET_S),
            identifier_sets={},
            arg_bindings=(),
            env_key_field="env",
        ),
        "producer": Node("producer", consumer=False),
        "consumer": Node("consumer", consumer=True),
    },
    deadline=timedelta(seconds=DEADLINE_S),
)


@trestle(deadline=60, env_arg="env")  # deadline: DEADLINE_S (a decorator argument is a literal)
def upstream_covered(ctx: Context, env: str = "dev") -> dict[str, str]:
    marker = FakeMarker(ctx.tmp / "markers", "run")
    ports = {ResourceCreate: marker, ResourceOwned: marker, ResourceReads: marker}
    run_tree(ctx, ENTRY, {"env": env}, ports=ports)
    return {"fixture": "upstream_covered", "env": env}
