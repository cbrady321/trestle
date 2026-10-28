"""The environment-lease fixture (L.SL-8.3; WR-OWN-8): a published workflow plugin whose declared
tree is one leaf that creates a fake marker, polls it ready and releases it, so each run's lane
holds one mutation interval (its create claim to its release). `env` is the environment argument
(`env_arg`, `env_key_field`: the D-b rule); `polls` is how many polls the marker needs before it
turns ready, which sets how long the interval is. Two runs of one environment must never hold
overlapping intervals. Written against the public unit-author surface of `trestle.workflow`."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from trestle_packs.fakes import FakeMarker

from trestle.plugin import Context, trestle
from trestle.workflow import (
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
from trestle.workflow.ports import (
    ResourceCreate,
    ResourceOwned,
    ResourceReads,
    ResourceSpec,
)
from trestle.workflow.units import (
    ActContext,
    Acted,
    EffectFacets,
    ObserveContext,
    ReadFacets,
    Step,
)
from trestle.workflow.values import (
    CheckResult,
    CreatedHandle,
    FoundRef,
    Observation,
    Verdict,
)

UNIT = "env_leaf"
CREATE_EFFECT = "up"
STOP_EFFECT = "stop"
SPEC = ResourceSpec("marker", RealizationKind.AGENT_LAUNCHED_PROJECT, "marker-entry", None)

# One CREATE + RUN target: at the published defaults B2-C2 (5) covers a release timeout of at most
# 4 s (finalization margin 35 s; see the SV-3.4 return), so the fixture keeps it small. The wait
# fits the budget with the release timeout (6 + 2 <= 8 s, L.SL-2.1's registration rule).
DECLARATION = LeafDeclaration(
    unit=UNIT,
    flags=LoopFlags(Compose.LEAF, CompletionSource.OBSERVED, Repeat.SAFE),
    preconditions=(),
    postcondition="ready",
    wait=WaitPolicy(timedelta(seconds=0.2), 1.0, timedelta(seconds=6)),
    resource_kind="marker",
    may_touch=frozenset({"marker"}),
    effects=(
        EffectDeclaration(
            effect=CREATE_EFFECT,
            facet=EffectFacetClass.CREATE,
            verb="",
            lifetime=Lifetime.RUN,
            host_sections=frozenset(),
            release_timeout=timedelta(seconds=2),
        ),
        EffectDeclaration(
            effect=STOP_EFFECT,
            facet=EffectFacetClass.OWNED,
            verb="",
            lifetime=Lifetime.RUN,
            host_sections=frozenset(),
            release_timeout=timedelta(seconds=2),
            is_release=True,
        ),
    ),
    retryable=frozenset(),
    remedies=(),
    budget=timedelta(seconds=8),
    max_attempts=2,
    env_key_field="env",
)


class SpineLeaf:
    """One resource: observe the marker, create it once, release it on the way out."""

    def declare(self) -> LeafDeclaration:
        return DECLARATION

    def observe(self, params: Any, reads: ReadFacets, ctx: ObserveContext) -> Observation:
        resource = reads.read(ResourceReads)
        seen = resource.observe(SPEC, ctx.lineage, CREATE_EFFECT)
        target = seen.selector_ref if seen.selector_ref is not None else (seen.found or (None,))[0]
        checked = resource.check("ready", target) if target is not None else None
        satisfied = checked is not None and checked.satisfied
        ctx.evidence.event(
            "env_leaf_observed", {"selector_present": seen.selector_present, "ready": satisfied}
        )
        return Observation(
            present=seen.selector_present or bool(seen.found),
            selector_present=seen.selector_present,
            identity_proven=seen.identity_proven,
            configuration_compatible=seen.configuration_compatible,
            postcondition=CheckResult(
                satisfied,
                None if checked is None else checked.code,
                "" if checked is None else checked.detail,
            ),
            preconditions=(),
            currency=(),
            found=tuple(FoundRef(f.resource_kind, f.selector, f.observed_at) for f in seen.found),
            code=seen.code,
            payload=None,
        )

    def advance(self, params: Any, state: Verdict, effects: EffectFacets, ctx: ActContext) -> Step:
        effects.create(ResourceCreate).create(SPEC, CREATE_EFFECT)
        return Acted()

    def release(self, params: Any, handle: CreatedHandle, effects: Any, ctx: ActContext) -> Step:
        effects.owned(ResourceOwned).stop(handle, STOP_EFFECT)
        return Acted()


ENTRY = WorkflowEntry(root=UNIT, units={UNIT: SpineLeaf()}, deadline=timedelta(seconds=60))


@trestle(deadline=60, env_arg="env")
def env_leaf(ctx: Context, env: str = "dev", polls: int = 8) -> dict[str, str]:
    marker = FakeMarker(ctx.tmp / "markers", "run", lag_polls=polls)
    # the fake marker is the one implementation of all three resource port families
    ports = {ResourceReads: marker, ResourceCreate: marker, ResourceOwned: marker}
    run_tree(ctx, ENTRY, {"env": env, "polls": polls}, ports=ports)
    return {"env": env}
