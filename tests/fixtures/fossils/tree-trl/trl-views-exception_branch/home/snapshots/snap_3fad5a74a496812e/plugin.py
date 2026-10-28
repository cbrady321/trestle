"""One node raises while its siblings are mid-wait (L.TR-3.6; MC-B3-01 behaviour fixture; consumed
by L.TR-L.8 and L.TR-L.9).

`app` runs `raiser`, `sibling_a` and `sibling_b` at once. The siblings create their marker and then
poll for a postcondition that never holds, so each is still converging when `raiser` (once both
siblings have created their markers, or after a cancellable bound) raises from `advance`. An
uncaught exception in any node flips the root's goal to RELEASE: the siblings record no further
non-release entry, are written `cut=STOPPED`, and the answer names the raising node as the
primary. Four vertices, depth 2."""

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
LABEL = {"vertices": 4, "depth": 2, "shared": None, "expect": "valid"}

CREATE_EFFECT = "up"
STOP_EFFECT = "stop"

LEAF_BUDGET_S = 10
ROOT_BUDGET_S = 20
DEADLINE_S = 36
PAUSE_BOUND_S = 8  # the raiser's bound on waiting for its siblings' markers: under LEAF_BUDGET_S
RUN: dict[str, Any] = {}  # the plugin function's marker handle, read by the raiser's condition


def _both_siblings_created() -> bool:
    """A node cannot observe a sibling's marker through its own lineage (the run-scoped selector
    embeds the node path), so the raiser reads the run's marker inventory, as creator_pk does."""
    return len(RUN["marker"].inventory()["containers"]) >= 2


class Node:
    """`raiser` waits for its siblings' markers, then raises from `advance`; a `sibling` creates
    its marker and polls for a postcondition that never holds."""

    def __init__(self, unit: str, *, raises: bool) -> None:
        self._unit = unit
        self._raises = raises
        self._paused = False
        # one logical system per node: a marker another node made is not this node's to find
        self._spec = ResourceSpec(
            unit, RealizationKind.AGENT_LAUNCHED_PROJECT, "exception-branch", None
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
        if self._raises and not self._paused:
            self._paused = True
            ctx.cancellation.wait_for(_both_siblings_created, timedelta(seconds=PAUSE_BOUND_S))
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
                ChildBinding(unit="sibling_a", params={}, needs=()),
                ChildBinding(unit="sibling_b", params={}, needs=()),
            ),
            concurrency=3,
            budget=timedelta(seconds=ROOT_BUDGET_S),
            identifier_sets={},
            arg_bindings=(),
            env_key_field="env",
        ),
        "raiser": Node("raiser", raises=True),
        "sibling_a": Node("sibling_a", raises=False),
        "sibling_b": Node("sibling_b", raises=False),
    },
    deadline=timedelta(seconds=DEADLINE_S),
)


@trestle(deadline=36, env_arg="env")  # deadline: DEADLINE_S (a decorator argument is a literal)
def exception_branch(ctx: Context, env: str = "dev") -> dict[str, str]:
    marker = FakeMarker(ctx.tmp / "markers", "run")
    RUN["marker"] = marker
    ports = {ResourceReads: marker, ResourceCreate: marker, ResourceOwned: marker}
    run_tree(ctx, ENTRY, {"env": env}, ports=ports)
    return {"env": env}
