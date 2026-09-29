"""The author library of the one-vertex spine fixture (L.SV-5.9).

A published plugin may import only the declaration data of `trestle.workflow` (R-PLUG-6), so the
work unit (which returns `Acted`, reads `Observation`s and binds facets) and the call to
`run_tree` live here, in a module the plugin imports the way a real workflow plugin imports its
pack. The plugin file itself (`spine_leaf.py`) holds only the declaration data, the fakes it
chooses and the `@trestle` function.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from typing import Any

from trestle.workflow import ports
from trestle.workflow.declarations import (
    CompletionSource,
    Compose,
    EffectDeclaration,
    EffectFacetClass,
    JsonValue,
    LeafDeclaration,
    Lifetime,
    LoopFlags,
    RealizationKind,
    Repeat,
    WaitPolicy,
    WorkflowEntry,
)
from trestle.workflow.loop import run_tree
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

UNIT = "spine_leaf"
CREATE_EFFECT = "up"
STOP_EFFECT = "stop"
OBSERVED_EVENT = "spine_observed"  # one evidence event per observe: the polls a test counts
LOGICAL_SYSTEM = "marker"
SPEC = ports.ResourceSpec(
    LOGICAL_SYSTEM, RealizationKind.AGENT_LAUNCHED_PROJECT, "marker-entry", None
)

# One CREATE + RUN target: at the published defaults B2-C2 (5) covers a release timeout of at
# most 4 s (finalization margin 35 s = stop bound 25 s + 5 s x timeout... see the SV-3.4 return).
RELEASE_TIMEOUT_S = 2.0
POLL_EVERY_S = 0.2
MAX_WAIT_S = 30.0
BUDGET_S = 8.0


def declaration(env_key_field: str | None = "env") -> LeafDeclaration:
    return LeafDeclaration(
        unit=UNIT,
        flags=LoopFlags(Compose.LEAF, CompletionSource.OBSERVED, Repeat.SAFE),
        preconditions=(),
        postcondition="ready",
        wait=WaitPolicy(timedelta(seconds=POLL_EVERY_S), 1.0, timedelta(seconds=MAX_WAIT_S)),
        resource_kind=LOGICAL_SYSTEM,
        may_touch=frozenset({LOGICAL_SYSTEM}),
        effects=(
            EffectDeclaration(
                effect=CREATE_EFFECT,
                facet=EffectFacetClass.CREATE,
                verb="",
                lifetime=Lifetime.RUN,
                host_sections=frozenset(),
                release_timeout=timedelta(seconds=RELEASE_TIMEOUT_S),
            ),
            EffectDeclaration(
                effect=STOP_EFFECT,
                facet=EffectFacetClass.OWNED,
                verb="",
                lifetime=Lifetime.RUN,
                host_sections=frozenset(),
                release_timeout=timedelta(seconds=RELEASE_TIMEOUT_S),
                is_release=True,
            ),
        ),
        retryable=frozenset(),
        remedies=(),
        budget=timedelta(seconds=BUDGET_S),
        max_attempts=2,
        env_key_field=env_key_field,
    )


class SpineLeaf:
    """One resource: observe the marker, create it once, release it on the way out."""

    def declare(self) -> LeafDeclaration:
        return declaration()

    def observe(self, params: Any, reads: ReadFacets, ctx: ObserveContext) -> Observation:
        resource = reads.read(ports.ResourceReads)
        seen = resource.observe(SPEC, ctx.lineage, CREATE_EFFECT)
        target = seen.selector_ref if seen.selector_ref is not None else (seen.found or (None,))[0]
        checked = resource.check("ready", target) if target is not None else None
        satisfied = checked is not None and checked.satisfied
        ctx.evidence.event(
            OBSERVED_EVENT, {"selector_present": seen.selector_present, "ready": satisfied}
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
        effects.create(ports.ResourceCreate).create(SPEC, CREATE_EFFECT)
        return Acted()

    def release(self, params: Any, handle: CreatedHandle, effects: Any, ctx: ActContext) -> Step:
        effects.owned(ports.ResourceOwned).stop(handle, STOP_EFFECT)
        return Acted()


def drive(
    ctx: Any,
    entry: WorkflowEntry,
    intent: Mapping[str, JsonValue],
    *,
    marker: Any,
) -> None:
    """The plugin callable's one call (B1-C9): the fake marker is the one implementation of all
    three resource port families."""
    run_tree(
        ctx,
        entry,
        intent,
        ports={
            ports.ResourceReads: marker,
            ports.ResourceCreate: marker,
            ports.ResourceOwned: marker,
        },
    )
