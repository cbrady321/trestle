"""The lagging-read fixture (L.SL-5.3): a published workflow plugin whose one leaf creates a fake
marker whose readback lags behind the claim, so a test can ask what the loop does while the read
has not caught up (WR-IDEM-5 `A-lagging-read-no-resubmit`).

Two lags, both on the fake marker of `trestle_packs.fakes`:

- `lag` is `FakeMarker(lag_polls=lag)`: the create is confirmed and the resource is visible, but
  its readiness check answers "not yet" for `lag` polls (an accepted step whose effect is not yet
  observable as complete).
- `hide` is the marker's read lag (`LaggingMarker`): the create answers UNKNOWN (accepted, the port
  cannot say it took effect) and the next `hide` observations do not show the resource at all.

`REPEAT` is the leaf's repeat class (a published declaration is one value: the `ONCE` variant is
this source with `REPEAT = Repeat.ONCE`; `declaration(repeat)` builds either for a unit test). A
`ONCE` leaf declares no release effect (B1-E1), so it creates a DURABLE marker; the `SAFE` leaf
creates a run-lifetime one and releases it. The unit is built by `make_unit` so a test can drive
it under its own clock and services (the counters record every call); the plugin below drives it
through `run_tree` like any published workflow."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from trestle_packs.fakes import (
    Confirmation,
    ConfirmationStatus,
    FakeMarker,
    ResourceObservation,
)

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

UNIT = "lagging_leaf"
CREATE_EFFECT = "up"
STOP_EFFECT = "stop"
SPEC = ResourceSpec("marker", RealizationKind.AGENT_LAUNCHED_PROJECT, "marker-entry", None)


def declaration(
    repeat: Repeat = Repeat.SAFE,
    *,
    max_attempts: int = 3,
    poll_every_s: float = 1.0,
    max_wait_s: float = 5.0,
    env_key_field: str | None = "env",
) -> LeafDeclaration:
    """One CREATE + RUN target on a flat wait policy (backoff 1.0); no retryable code, no remedy,
    so the only way this leaf ever issues a second claim is the join's own UNKNOWN rows."""
    once = repeat is Repeat.ONCE
    create = EffectDeclaration(
        effect=CREATE_EFFECT,
        facet=EffectFacetClass.CREATE,
        verb="",
        lifetime=Lifetime.DURABLE if once else Lifetime.RUN,
        host_sections=frozenset(),
        release_timeout=None if once else timedelta(seconds=2),
    )
    stop = EffectDeclaration(
        effect=STOP_EFFECT,
        facet=EffectFacetClass.OWNED,
        verb="",
        lifetime=Lifetime.RUN,
        host_sections=frozenset(),
        release_timeout=timedelta(seconds=2),
        is_release=True,
    )
    return LeafDeclaration(
        unit=UNIT,
        flags=LoopFlags(Compose.LEAF, CompletionSource.OBSERVED, repeat),
        preconditions=(),
        postcondition="ready",
        wait=WaitPolicy(timedelta(seconds=poll_every_s), 1.0, timedelta(seconds=max_wait_s)),
        resource_kind="marker",
        may_touch=frozenset({"marker"}),
        effects=(create,) if once else (create, stop),
        retryable=frozenset(),
        remedies=(),
        budget=timedelta(seconds=45),
        max_attempts=max_attempts,
        env_key_field=env_key_field,
    )


class LaggingMarker(FakeMarker):
    """A marker whose creation is accepted (UNKNOWN: the port cannot say it took effect) and whose
    readback shows nothing for the next `hide` observations after each create. `creates` counts
    the claims that reached the port."""

    def __init__(self, *args: Any, hide: int = 0, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.hide = hide
        self.creates = 0
        self._hidden_left = 0

    def create(self, spec: Any, ticket: Any) -> Confirmation:
        self.creates += 1
        made = super().create(spec, ticket)
        if self.hide <= 0:
            return made
        self._hidden_left = self.hide
        return Confirmation(ConfirmationStatus.UNKNOWN, None, made.identity)

    def observe(self, spec: Any, lineage: Any, effect: str | None) -> ResourceObservation:
        seen = super().observe(spec, lineage, effect)
        if self._hidden_left > 0 and seen.selector_present:
            self._hidden_left -= 1
            return ResourceObservation(False, None, False, True, (), (), None)
        return seen


class LaggingLeaf:
    """Observe the marker, create it once per claim, release it on the way out (a SAFE leaf)."""

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


def make_unit(decl: LeafDeclaration | None = None) -> LaggingLeaf:
    return LaggingLeaf(decl)


REPEAT = Repeat.SAFE
ENTRY = WorkflowEntry(
    root=UNIT, units={UNIT: LaggingLeaf(declaration(REPEAT))}, deadline=timedelta(seconds=120)
)


@trestle(deadline=120, env_arg="env")
def lagging_leaf(ctx: Context, env: str = "dev", lag: int = 3, hide: int = 0) -> dict[str, str]:
    marker = LaggingMarker(ctx.tmp / "markers", "run", lag_polls=lag, hide=hide)
    ports = {ResourceReads: marker, ResourceCreate: marker, ResourceOwned: marker}
    run_tree(ctx, ENTRY, {"env": env, "lag": lag, "hide": hide}, ports=ports)
    return {"env": env, "lag": str(lag), "hide": str(hide)}
