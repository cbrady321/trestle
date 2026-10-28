"""The remedy fixture (L.SL-6.1): a published workflow plugin whose one leaf creates a fake marker
that is unhealthy until it is restarted, and declares that repair as a remedy (V-14
`RemedyDeclaration`: trigger code `marker.hot`, the OWNED effect `restart`).

The marker (`RemedyMarker`, a `trestle_packs.fakes.FakeMarker`) starts unready and reports the
trigger code on every unsatisfied check until `needed` repairs have been made; the unit copies the
check's code to `Observation.code`, which is what J-20 reads (V-3.1). The knobs pick the scene:

- `fixed` is `FakeMarker(fixed_fingerprint=True)`: a repair never changes the reported code and the
  marker never becomes ready, so the trigger code persists after the remedy's confirmed ticket
  (J-3a gives `REMEDY_NO_PROGRESS`, or J-3 `REMEDY_EXHAUSTED` when the remedy's attempts are already
  spent).
- `trigger` is the code the unready marker reports; a code the declaration does not list is never
  remedied (WR-REMEDY-1).
- `lag` is the polls the marker stays unready after a repair (`lag_polls`, restarted by a repair).
- `restart_status` / `restart_code` script what `restart` answers (the repair is refused when it
  answers NOT_APPLIED; nothing is repaired).

`declaration(...)` builds the leaf's declaration for a unit test that drives it under its own
clock and services (`make_unit`); the plugin below drives it through `run_tree` like any published
workflow. The unit calls `restart` only when the loop grants a remedy (`ActContext.remedy` through
`state.remedy`, B1-C3) and creates the marker otherwise."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from trestle_packs.fakes import (
    CheckResult,
    Confirmation,
    ConfirmationStatus,
    FakeMarker,
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
    RemedyDeclaration,
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
    CheckResult as UnitCheckResult,
)
from trestle.workflow.values import (
    CreatedHandle,
    FoundRef,
    Observation,
    Verdict,
)

UNIT = "remedy_leaf"
CREATE_EFFECT = "up"
RESTART_EFFECT = "restart"
STOP_EFFECT = "stop"
HOT = "marker.hot"
RETRYABLE = "marker.busy"
SPEC = ResourceSpec("marker", RealizationKind.AGENT_LAUNCHED_PROJECT, "marker-entry", None)
MAX_ATTEMPTS = 3


def declaration(
    *,
    remedy_attempts: int = 2,
    remedy_total_s: float = 6.0,
    cooldown_s: float = 0.5,
    poll_every_s: float = 0.2,
    max_wait_s: float = 1.0,
    max_attempts: int = MAX_ATTEMPTS,
    retryable: frozenset[str] = frozenset(),
    env_key_field: str | None = "env",
) -> LeafDeclaration:
    """One CREATE + OWNED restart remedy + release on a flat wait policy (backoff 1.0)."""
    return LeafDeclaration(
        unit=UNIT,
        flags=LoopFlags(Compose.LEAF, CompletionSource.OBSERVED, Repeat.SAFE),
        preconditions=(),
        postcondition="ready",
        wait=WaitPolicy(timedelta(seconds=poll_every_s), 1.0, timedelta(seconds=max_wait_s)),
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
                effect=RESTART_EFFECT,
                facet=EffectFacetClass.OWNED,
                verb="",
                lifetime=Lifetime.RUN,
                host_sections=frozenset(),
                release_timeout=None,
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
        retryable=retryable,
        remedies=(
            RemedyDeclaration(
                code=HOT,
                effect=RESTART_EFFECT,
                attempts=remedy_attempts,
                total=timedelta(seconds=remedy_total_s),
                cooldown=timedelta(seconds=cooldown_s),
            ),
        ),
        budget=timedelta(seconds=45),
        max_attempts=max_attempts,
        env_key_field=env_key_field,
    )


class RemedyMarker(FakeMarker):
    """A marker that is unready, reporting `trigger` (`outcome_codes[0]`), until `needed` repairs
    were made; afterwards the fake's own knobs apply (`lag_polls` after a repair, `never_ready` and
    `fixed_fingerprint` keep it unready with the same code). `restart` answers `restart_status`
    (NOT_APPLIED repairs nothing); `restarts` counts the calls."""

    def __init__(
        self,
        *args: Any,
        trigger: str = HOT,
        needed: int = 1,
        restart_status: ConfirmationStatus = ConfirmationStatus.APPLIED,
        restart_code: str | None = None,
        **kwargs: Any,
    ) -> None:
        kwargs.setdefault("outcome_codes", (trigger,))
        super().__init__(*args, **kwargs)
        self.needed = needed
        self.restart_status = restart_status
        self.restart_code = restart_code
        self.restarts = 0

    def check(self, check: str, target: Any) -> CheckResult:
        selector = target.selector
        if self._record(selector) is not None and self._repairs.get(selector, 0) < self.needed:
            self._polls[selector] = self._polls.get(selector, 0) + 1
            return CheckResult(False, self._code(selector), f"{check} not yet")
        return super().check(check, target)

    def restart(self, target: Any, ticket: Any) -> Confirmation:
        self.restarts += 1
        if self.restart_status is not ConfirmationStatus.APPLIED:
            return Confirmation(self.restart_status, self.restart_code, None)
        return super().restart(target, ticket)


class RemedyLeaf:
    """Observe the marker, create it, restart it under a granted remedy, release it on the way
    out; every call is counted."""

    def __init__(self, decl: LeafDeclaration | None = None) -> None:
        self.decl = decl or declaration()
        self.observes = 0
        self.advances = 0
        self.releases = 0
        self.log: list[str] = []

    def declare(self) -> LeafDeclaration:
        return self.decl

    def observe(self, params: Any, reads: ReadFacets, ctx: ObserveContext) -> Observation:
        self.observes += 1
        self.log.append("observe")
        resource = reads.read(ResourceReads)
        seen = resource.observe(SPEC, ctx.lineage, CREATE_EFFECT)
        target = seen.selector_ref if seen.selector_ref is not None else (seen.found or (None,))[0]
        checked = resource.check("ready", target) if target is not None else None
        satisfied = checked is not None and checked.satisfied
        return Observation(
            present=seen.selector_present or bool(seen.found),
            selector_present=seen.selector_present,
            identity_proven=seen.identity_proven,
            configuration_compatible=seen.configuration_compatible,
            postcondition=UnitCheckResult(
                satisfied,
                None if checked is None else checked.code,
                "" if checked is None else checked.detail,
            ),
            preconditions=(),
            currency=(),
            found=tuple(FoundRef(f.resource_kind, f.selector, f.observed_at) for f in seen.found),
            # J-20 / J-23 read this code on a present observation (V-3.1): the check's own
            code=seen.code
            if seen.code is not None
            else (None if checked is None else checked.code),
            payload=None,
        )

    def advance(self, params: Any, state: Verdict, effects: EffectFacets, ctx: ActContext) -> Step:
        self.advances += 1
        if state.remedy is not None and state.owned:
            self.log.append("remedy")
            effects.owned(ResourceOwned).restart(state.owned[-1], state.remedy.effect)
        else:
            self.log.append("advance")
            effects.create(ResourceCreate).create(SPEC, CREATE_EFFECT)
        return Acted()

    def release(self, params: Any, handle: CreatedHandle, effects: Any, ctx: ActContext) -> Step:
        self.releases += 1
        effects.owned(ResourceOwned).stop(handle, STOP_EFFECT)
        return Acted()


def make_unit(decl: LeafDeclaration | None = None) -> RemedyLeaf:
    return RemedyLeaf(decl)


ENTRY = WorkflowEntry(
    root=UNIT, units={UNIT: RemedyLeaf(declaration())}, deadline=timedelta(seconds=120)
)


@trestle(deadline=120, env_arg="env")
def remedy_leaf(
    ctx: Context, env: str = "dev", lag: int = 1, fixed: bool = False, trigger: str = HOT
) -> dict[str, str]:
    marker = RemedyMarker(
        ctx.tmp / "markers",
        "run",
        lag_polls=lag,
        never_ready=fixed,
        fixed_fingerprint=fixed,
        trigger=trigger,
    )
    ports = {ResourceReads: marker, ResourceCreate: marker, ResourceOwned: marker}
    run_tree(ctx, ENTRY, {"env": env, "lag": lag, "fixed": fixed, "trigger": trigger}, ports=ports)
    return {"env": env, "lag": str(lag), "fixed": str(fixed), "trigger": trigger}
