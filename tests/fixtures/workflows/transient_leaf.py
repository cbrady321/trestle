"""The transient-failure fixture (L.SL-4.1): a published workflow plugin whose declared tree is one
leaf over the fakes of `trestle_packs.fakes`, whose one CREATE effect fails and is retried.

`mode` picks what the marker's `create` does on its first `fails` calls: `cure` answers
NOT_APPLIED with the declared-retryable code `marker.transient` (the retry cures it), `undeclared`
answers NOT_APPLIED with a code the declaration does not list (never retried: the node FAILS with
that code), `unknown` answers UNKNOWN (the effect may have taken; a `ONCE` leaf is BLOCKED with
`execution.effect_unconfirmed` and never issues again). The declaration is one published value:
the `once` variant is this source with `REPEAT = Repeat.ONCE` (a ONCE leaf declares no release
effect, so its marker is DURABLE). Everything else is `spine_leaf`'s: the same declaration
shape, written against the public unit-author surface, one `run_tree` call. The unit emits one
`transient_observed` evidence event per observation."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from trestle_packs.fakes import Confirmation, ConfirmationStatus, FakeMarker

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

UNIT = "transient_leaf"
CREATE_EFFECT = "up"
STOP_EFFECT = "stop"
RETRYABLE = "marker.transient"
UNDECLARED = "marker.other"
REPEAT = Repeat.SAFE
MAX_ATTEMPTS = 3
SPEC = ResourceSpec("marker", RealizationKind.AGENT_LAUNCHED_PROJECT, "marker-entry", None)


def declaration(repeat: Repeat, max_attempts: int) -> LeafDeclaration:
    """A `ONCE` leaf declares no release effect (B1-E1) and so creates a DURABLE marker; the
    `SAFE` leaf creates a run-lifetime one and releases it."""
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
        wait=WaitPolicy(timedelta(seconds=0.2), 1.0, timedelta(seconds=3)),
        resource_kind="marker",
        may_touch=frozenset({"marker"}),
        effects=(create,) if once else (create, stop),
        retryable=frozenset({RETRYABLE}),
        remedies=(),
        budget=timedelta(seconds=8),
        max_attempts=max_attempts,
        env_key_field="env",
    )


class TransientMarker(FakeMarker):
    """The fake marker whose `create` fails before taking effect on its first `fails` calls."""

    def __init__(self, *args: Any, mode: str, fails: int, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._mode = mode
        self._fails = fails
        self.creates = 0

    def create(self, spec: Any, ticket: Any) -> Confirmation:
        self.creates += 1
        if self.creates <= self._fails:
            if self._mode == "unknown":
                return Confirmation(ConfirmationStatus.UNKNOWN, None, None)
            code = UNDECLARED if self._mode == "undeclared" else RETRYABLE
            return Confirmation(ConfirmationStatus.NOT_APPLIED, code, None)
        return super().create(spec, ticket)


class TransientLeaf:
    """One resource: observe the marker, create it (retrying), release it on the way out."""

    def declare(self) -> LeafDeclaration:
        return declaration(REPEAT, MAX_ATTEMPTS)

    def observe(self, params: Any, reads: ReadFacets, ctx: ObserveContext) -> Observation:
        resource = reads.read(ResourceReads)
        seen = resource.observe(SPEC, ctx.lineage, CREATE_EFFECT)
        target = seen.selector_ref if seen.selector_ref is not None else (seen.found or (None,))[0]
        checked = resource.check("ready", target) if target is not None else None
        satisfied = checked is not None and checked.satisfied
        ctx.evidence.event(
            "transient_observed", {"selector_present": seen.selector_present, "ready": satisfied}
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


ENTRY = WorkflowEntry(root=UNIT, units={UNIT: TransientLeaf()}, deadline=timedelta(seconds=60))


@trestle(deadline=60, env_arg="env")
def transient_leaf(
    ctx: Context, env: str = "dev", mode: str = "cure", fails: int = 1
) -> dict[str, str]:
    marker = TransientMarker(ctx.tmp / "markers", "run", mode=mode, fails=fails)
    ports = {ResourceReads: marker, ResourceCreate: marker, ResourceOwned: marker}
    run_tree(ctx, ENTRY, {"env": env, "mode": mode}, ports=ports)
    return {"env": env, "mode": mode}
