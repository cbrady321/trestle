"""The three-step tree that runs, with one retry and one remediation (L.TR-L.3; MC-B3-01 behaviour
fixture; WR-TERM-1). The structural twin is `three_step_retry_remedy` (declaration only).

`build` -> `deploy` -> `verify`, each a leaf that needs the one before, run one at a time. Each leaf
creates a run-lifetime marker on the fake and polls until it is ready (two polls each, so the run
takes a few seconds and outlives one bounded wait). `deploy` is the interesting one:

- its first `create` is refused with the declared-retryable code `transient` (NOT_APPLIED, so
  nothing was created): the loop issues the create again (the retry);
- once created its marker reports the declared remedy trigger `not_started` until it is restarted:
  the loop grants the one declared remedy, `advance` runs the OWNED `restart` effect, and the marker
  becomes ready (the remediation, answered as disposition `repaired`).

Four vertices, depth 2."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from trestle_packs.fakes import CheckResult, Confirmation, ConfirmationStatus, FakeMarker

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
    RemedyDeclaration,
    Repeat,
    WaitPolicy,
    WorkflowEntry,
)
from trestle.workflow.loop import run_tree
from trestle.workflow.ports import ResourceCreate, ResourceOwned, ResourceReads, ResourceSpec
from trestle.workflow.units import ActContext, Acted, EffectFacets, ObserveContext, ReadFacets, Step
from trestle.workflow.values import CheckResult as UnitCheckResult
from trestle.workflow.values import CreatedHandle, FoundRef, Observation, Verdict

# MC-B3-01. `vertices` counts the distinct logical nodes the declaration references; `depth` is
# the longest containment chain, a leaf being depth 1; `shared` names the node two parents
# reference, or None; `expect` is "valid" or the ground the tree is defective in.
LABEL = {"vertices": 4, "depth": 2, "shared": None, "expect": "valid"}

CREATE_EFFECT = "up"
RESTART_EFFECT = "restart"
STOP_EFFECT = "stop"
TRANSIENT = "transient"  # deploy's retryable create refusal
NOT_STARTED = "not_started"  # deploy's remedy trigger
FLAKY_UNIT = "deploy"

POLL_S = 1  # one flat poll interval; every leaf waits `LAG_POLLS` of them, so ~2 s a step
LAG_POLLS = 2
WAIT_MAX_S = 4
REMEDY_TOTAL_S = 5
RELEASE_TIMEOUT_S = 1  # three RUN targets in three ranks stay inside the finalization margin
LEAF_BUDGET_S = 15  # wait + remedy total + release fit inside it (publication checks)
ROOT_BUDGET_S = 60
DEADLINE_S = 120


class DeployMarker(FakeMarker):
    """The fake marker, except that `deploy`'s first `create` is refused with `transient` and its
    marker reports `not_started` until it has been restarted once."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._deploy_creates = 0

    def create(self, spec: Any, ticket: Any) -> Confirmation:
        if spec.logical_system == FLAKY_UNIT:
            self._deploy_creates += 1
            if self._deploy_creates == 1:
                return Confirmation(ConfirmationStatus.NOT_APPLIED, TRANSIENT, None)
        return super().create(spec, ticket)

    def check(self, check: str, target: Any) -> CheckResult:
        selector = target.selector
        record = self._record(selector)
        if record is not None and record.get("system") == FLAKY_UNIT:
            if self._repairs.get(selector, 0) < 1:
                self._polls[selector] = self._polls.get(selector, 0) + 1
                return CheckResult(False, NOT_STARTED, f"{check} not yet: not started")
        return super().check(check, target)


class Leaf:
    """Observe the marker, create it (or restart it under a granted remedy), release it on the
    way out."""

    def __init__(self, unit: str) -> None:
        self._unit = unit
        self._spec = ResourceSpec(unit, RealizationKind.AGENT_LAUNCHED_PROJECT, "three-step", None)

    def declare(self) -> LeafDeclaration:
        flaky = self._unit == FLAKY_UNIT
        effects = [
            EffectDeclaration(
                CREATE_EFFECT,
                EffectFacetClass.CREATE,
                "",
                Lifetime.RUN,
                frozenset(),
                timedelta(seconds=RELEASE_TIMEOUT_S),
            ),
            EffectDeclaration(
                STOP_EFFECT,
                EffectFacetClass.OWNED,
                "",
                Lifetime.RUN,
                frozenset(),
                timedelta(seconds=RELEASE_TIMEOUT_S),
                is_release=True,
            ),
        ]
        remedies: tuple[RemedyDeclaration, ...] = ()
        if flaky:
            effects.insert(
                1,
                EffectDeclaration(
                    RESTART_EFFECT,
                    EffectFacetClass.OWNED,
                    "",
                    Lifetime.RUN,
                    frozenset(),
                    None,
                ),
            )
            remedies = (
                RemedyDeclaration(
                    code=NOT_STARTED,
                    effect=RESTART_EFFECT,
                    attempts=1,
                    total=timedelta(seconds=REMEDY_TOTAL_S),
                    cooldown=timedelta(seconds=1),
                ),
            )
        return LeafDeclaration(
            unit=self._unit,
            flags=LoopFlags(Compose.LEAF, CompletionSource.OBSERVED, Repeat.SAFE),
            preconditions=(),
            postcondition="ready",
            wait=WaitPolicy(timedelta(seconds=POLL_S), 1.0, timedelta(seconds=WAIT_MAX_S)),
            resource_kind="marker",
            may_touch=frozenset({"marker"}),
            effects=tuple(effects),
            retryable=frozenset({TRANSIENT}) if flaky else frozenset(),
            remedies=remedies,
            budget=timedelta(seconds=LEAF_BUDGET_S),
            max_attempts=3 if flaky else 1,
        )

    def observe(self, params: Any, reads: ReadFacets, ctx: ObserveContext) -> Observation:
        resource = reads.read(ResourceReads)
        seen = resource.observe(self._spec, ctx.lineage, CREATE_EFFECT)
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
            # J-20 / J-23 read this code on a present observation: the check's own
            code=seen.code
            if seen.code is not None
            else (None if checked is None else checked.code),
            payload=None,
        )

    def advance(self, params: Any, state: Verdict, effects: EffectFacets, ctx: ActContext) -> Step:
        if state.remedy is not None and state.owned:
            effects.owned(ResourceOwned).restart(state.owned[-1], state.remedy.effect)
        else:
            effects.create(ResourceCreate).create(self._spec, CREATE_EFFECT)
        return Acted()

    def release(self, params: Any, handle: CreatedHandle, effects: Any, ctx: ActContext) -> Step:
        effects.owned(ResourceOwned).stop(handle, STOP_EFFECT)
        return Acted()


ENTRY = WorkflowEntry(
    root="pipeline",
    units={
        "pipeline": AllDeclaration(
            unit="pipeline",
            flags=LoopFlags(Compose.ALL, CompletionSource.OBSERVED, Repeat.SAFE),
            children=(
                ChildBinding(unit="build", params={}, needs=()),
                ChildBinding(unit="deploy", params={}, needs=("build",)),
                ChildBinding(unit="verify", params={}, needs=("deploy",)),
            ),
            concurrency=1,
            budget=timedelta(seconds=ROOT_BUDGET_S),
            identifier_sets={},
            arg_bindings=(),
            env_key_field="env",
        ),
        "build": Leaf("build"),
        "deploy": Leaf("deploy"),
        "verify": Leaf("verify"),
    },
    deadline=timedelta(seconds=DEADLINE_S),
)


@trestle(deadline=120, env_arg="env")  # deadline: DEADLINE_S (a decorator argument is a literal)
def three_step_live(ctx: Context, env: str = "dev") -> dict[str, str]:
    marker = DeployMarker(ctx.tmp / "markers", "run", lag_polls=LAG_POLLS)
    ports = {ResourceReads: marker, ResourceCreate: marker, ResourceOwned: marker}
    run_tree(ctx, ENTRY, {"env": env}, ports=ports)
    return {"env": env}
