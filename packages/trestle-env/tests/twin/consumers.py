"""Credential consumers for the stale-generation proof (L.RB-9.5; B3-C9, B3-C11, J-13, J-22).

`ConsumerUnit` is a leaf whose resource is a container that consumes the demo credential from its
mounted channel (`trestle_packs.grant`, D-9: DEMO ONLY). Each observation reads, from inside the
consumer, which generation it authenticates with (`GrantReads.observe_in_consumer`) and turns it
into the contract's currency fact (`ports.grant_currency`): the issuer alone says a generation is
older (`CREDENTIAL_STALE`). The join does the rest:

* an OWNED consumer (this run created it) holding an older generation gets the declared remedy
  (J-22): the credential is re-delivered into its channel in place (`GrantDelivery.deliver` on the
  same handle, a repair, never a release). A file-mounted credential's stale fix is re-delivery,
  not recreation (the decision of 2026-09-30; the plan's title said "recreated").
* a FOUND consumer (not this run's) whose identity is proven and whose generation is older ends
  INCOMPATIBLE (J-13), classed BLOCKED, and nothing acts on it.

A found consumer's identity is read from inside it (`CONSUMER_IDENTITY`, an exec check that its
channel file is there), never assumed from its name. The loop feeds no host-scope readings of its
own, so a proof run sets `walked.host_scope` to a live scope over the same issuer (`LiveScope`, as
L.RB-9.3's proof does); without it every current fact would read as stale.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any, Final

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
    codes,
    ports,
)
from trestle.workflow.declarations import RemedyDeclaration
from trestle.workflow.ports import (
    GrantDelivery,
    GrantReads,
    ResourceCreate,
    ResourceOwned,
    ResourceReads,
    ResourceSpec,
)
from trestle.workflow.units import ActContext, Acted, EffectFacets, ObserveContext, Step
from trestle.workflow.values import CheckResult, CreatedHandle, FoundRef, Observation, Verdict

UP: Final = "up"
DELIVER: Final = "deliver"
STOP: Final = "stop"
CONSUMER_IDENTITY: Final = "consumer_identity"
CONSUMER_ENTRY: Final = "consumer"
IDENTITY_ARGV: Final = ("sh", "-c", "test -r /run/trestle-demo-credentials/credentials")
BUDGET_S: Final = 60
WAIT_S: Final = 20
RELEASE_TIMEOUT_S: Final = 5


def consumer_spec(system: str) -> ResourceSpec:
    return ResourceSpec(system, RealizationKind.DOCKER_SERVICE, CONSUMER_ENTRY, None)


class ConsumerUnit:
    """A credential consumer container: created by the run, or found under `system`'s name."""

    def __init__(self, unit: str, system: str) -> None:
        self._unit = unit
        self._spec = consumer_spec(system)

    def declare(self) -> LeafDeclaration:
        release = timedelta(seconds=RELEASE_TIMEOUT_S)
        return LeafDeclaration(
            unit=self._unit,
            flags=LoopFlags(Compose.LEAF, CompletionSource.OBSERVED, Repeat.SAFE),
            preconditions=(),
            postcondition="credential_current",
            wait=WaitPolicy(timedelta(seconds=1), 1.0, timedelta(seconds=WAIT_S)),
            resource_kind="docker_container",
            may_touch=frozenset({"docker_container"}),
            effects=(
                EffectDeclaration(
                    UP, EffectFacetClass.CREATE, "", Lifetime.RUN, frozenset(), release
                ),
                EffectDeclaration(
                    DELIVER, EffectFacetClass.OWNED, "", Lifetime.RUN, frozenset(), release
                ),
                EffectDeclaration(
                    STOP,
                    EffectFacetClass.OWNED,
                    "",
                    Lifetime.RUN,
                    frozenset(),
                    release,
                    is_release=True,
                ),
            ),  # fmt: skip
            retryable=frozenset(),
            remedies=(
                RemedyDeclaration(
                    codes.CREDENTIAL_STALE, DELIVER, 1, timedelta(seconds=WAIT_S), timedelta(0)
                ),
            ),
            budget=timedelta(seconds=BUDGET_S),
            max_attempts=2,
        )

    def observe(self, params: Any, reads: Any, ctx: ObserveContext) -> Observation:
        resource = reads.read(ResourceReads)
        seen = resource.observe(self._spec, ctx.lineage, UP)
        target: Any = seen.selector_ref if seen.selector_ref is not None else None
        identity, configuration = seen.identity_proven, seen.configuration_compatible
        if target is None and seen.found and seen.code is None:
            target = seen.found[0]  # a found consumer: its identity is read, never assumed
            identity = resource.check(CONSUMER_IDENTITY, target).satisfied
            configuration = True
        currency: tuple[Any, ...] = ()
        ready = CheckResult(False, None, "")
        if target is not None and seen.code is None:
            grant = reads.read(GrantReads)
            consumer = grant.observe_in_consumer(target)
            fact = ports.grant_currency(consumer, grant.observe_host())
            currency = (fact,) if fact is not None else ()
            ready = CheckResult(consumer.authenticated, None, "")
        return Observation(
            present=seen.selector_present or bool(seen.found),
            selector_present=seen.selector_present,
            identity_proven=identity,
            configuration_compatible=configuration,
            postcondition=ready,
            preconditions=(),
            currency=currency,
            found=tuple(FoundRef(f.resource_kind, f.selector, f.observed_at) for f in seen.found),
            code=seen.code,
            payload=None,
        )

    def advance(self, params: Any, state: Verdict, effects: EffectFacets, ctx: ActContext) -> Step:
        if state.remedy is not None and state.owned:
            effects.owned(GrantDelivery).deliver(state.owned[-1], state.remedy.effect)
        else:
            effects.create(ResourceCreate).create(self._spec, UP)
        return Acted()

    def release(self, params: Any, handle: CreatedHandle, effects: Any, ctx: ActContext) -> Step:
        effects.owned(ResourceOwned).stop(handle, STOP)
        return Acted()


class LiveScope:
    """The host scope the join compares a consumer's generation with, read live from the issuer
    at each join (`DemoHostScope`; duck-typed: the join reads `.readings`)."""

    def __init__(self, grant: Any, now: Callable[[], datetime]) -> None:
        from trestle_packs.grant import DemoHostScope

        self._scope = DemoHostScope(grant, now=now)

    @property
    def readings(self) -> Any:
        return self._scope.readings().readings
