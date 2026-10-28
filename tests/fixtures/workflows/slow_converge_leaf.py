"""A leaf that takes many polls to converge (L.SL-5.1): a published workflow plugin whose one
declared leaf creates a fake marker once and then waits, on a backing-off wait policy, until the
marker turns ready. It shows that the loop polls on the declared policy and never re-invokes
`advance` to wait, and how an elapsed `max_wait` ends the node.

`env` is the environment argument (`env_arg`, WR-OWN-8). `lag` is how many unsatisfied
readiness checks the marker answers before it is ready; `never` keeps it unready for good, so
`max_wait` elapses (J-15). The unit is built by `make_unit` so a test can drive it under its own
clock and services (the `SlowConvergeLeaf` counters record every call); the plugin below drives
it through `run_tree` over `FakeMarker` like any published workflow."""

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

UNIT = "slow_converge_leaf"
CREATE_EFFECT = "up"
STOP_EFFECT = "stop"
SPEC = ResourceSpec("marker", RealizationKind.AGENT_LAUNCHED_PROJECT, "marker-entry", None)


def declaration(
    *,
    poll_every_s: float = 0.2,
    backoff: float = 2.0,
    max_wait_s: float = 30.0,
    env_key_field: str | None = "env",
) -> LeafDeclaration:
    """One CREATE + RUN target on `WaitPolicy(poll_every, backoff, max_wait)`; a test that admits
    the unit without a request passes `env_key_field=None`."""
    return LeafDeclaration(
        unit=UNIT,
        flags=LoopFlags(Compose.LEAF, CompletionSource.OBSERVED, Repeat.SAFE),
        preconditions=(),
        postcondition="ready",
        wait=WaitPolicy(timedelta(seconds=poll_every_s), backoff, timedelta(seconds=max_wait_s)),
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
        budget=timedelta(seconds=45),
        max_attempts=2,
        env_key_field=env_key_field,
    )


class SlowConvergeLeaf:
    """Observe the marker, create it once, release it on the way out; count every call."""

    def __init__(self, decl: LeafDeclaration | None = None) -> None:
        self.decl = decl or declaration()
        self.observes = 0
        self.advances = 0
        self.releases = 0

    def declare(self) -> LeafDeclaration:
        return self.decl

    def observe(self, params: Any, reads: ReadFacets, ctx: ObserveContext) -> Observation:
        self.observes += 1
        resource = reads.read(ResourceReads)
        seen = resource.observe(SPEC, ctx.lineage, CREATE_EFFECT)
        target = seen.selector_ref if seen.selector_ref is not None else (seen.found or (None,))[0]
        checked = resource.check("ready", target) if target is not None else None
        return Observation(
            present=seen.selector_present or bool(seen.found),
            selector_present=seen.selector_present,
            identity_proven=seen.identity_proven,
            configuration_compatible=seen.configuration_compatible,
            postcondition=CheckResult(
                checked is not None and checked.satisfied,
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
        self.advances += 1
        effects.create(ResourceCreate).create(SPEC, CREATE_EFFECT)
        return Acted()

    def release(self, params: Any, handle: CreatedHandle, effects: Any, ctx: ActContext) -> Step:
        self.releases += 1
        effects.owned(ResourceOwned).stop(handle, STOP_EFFECT)
        return Acted()


def make_unit(decl: LeafDeclaration | None = None) -> SlowConvergeLeaf:
    return SlowConvergeLeaf(decl)


ENTRY = WorkflowEntry(root=UNIT, units={UNIT: SlowConvergeLeaf()}, deadline=timedelta(seconds=120))


@trestle(deadline=120, env_arg="env")
def slow_converge_leaf(
    ctx: Context, env: str = "dev", lag: int = 3, never: bool = False
) -> dict[str, str]:
    marker = FakeMarker(ctx.tmp / "markers", "run", lag_polls=lag, never_ready=never)
    ports = {ResourceReads: marker, ResourceCreate: marker, ResourceOwned: marker}
    run_tree(ctx, ENTRY, {"env": env, "lag": lag, "never": never}, ports=ports)
    return {"env": env, "lag": str(lag), "never": str(never)}
