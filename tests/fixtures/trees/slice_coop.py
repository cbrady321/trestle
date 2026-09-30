"""A cooperating slow child (L.TR-3.3; MC-B3-01 behaviour fixture; consumed by L.TR-L.5).

`app` runs `quick`, which is ready as soon as it is made, and `slow`, which never turns ready and
polls (through the loop's own wait, which is the cancel signal's): it cooperates, so the loop ends
it at the slice the plan carved for it, `StepEntry(FAILED, CARVE_EXCEEDED)` naming its path. Three
vertices, depth 2. `quick` ends satisfied either way; the run's answer is `TIMED_OUT`."""

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
LABEL = {"vertices": 3, "depth": 2, "shared": None, "expect": "valid"}

CREATE_EFFECT = "up"
STOP_EFFECT = "stop"
SPEC = ResourceSpec("marker", RealizationKind.AGENT_LAUNCHED_PROJECT, "slice-coop", None)

# The plan carves each child the root's budget less the finalization reserve (10 s), ending 20 s
# before the deadline: `slow` gets 16 s, so its slice ends 16 s after the run starts. `slow` holds
# its first observation for STALL_S, so its own wait (12 s) would end after the slice does.
QUICK_BUDGET_S = 10
SLOW_BUDGET_S = 16
ROOT_BUDGET_S = 26
DEADLINE_S = 36
SLOW_WAIT_S = 12
STALL_S = 8


def _declaration(unit: str, budget_s: int, max_wait_s: int) -> LeafDeclaration:
    return LeafDeclaration(
        unit=unit,
        flags=LoopFlags(Compose.LEAF, CompletionSource.OBSERVED, Repeat.SAFE),
        preconditions=(),
        postcondition="ready",
        wait=WaitPolicy(timedelta(seconds=1), 1.0, timedelta(seconds=max_wait_s)),
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
        budget=timedelta(seconds=budget_s),
        max_attempts=1,
    )


class Leaf:
    """Observe the node's marker, create it once, release it on the way out. `patient` is a leaf
    that never turns ready, however long it is polled."""

    def __init__(self, unit: str, budget_s: int, max_wait_s: int, *, patient: bool) -> None:
        self._decl = _declaration(unit, budget_s, max_wait_s)
        self._patient = patient
        self._stalled = False

    def declare(self) -> LeafDeclaration:
        return self._decl

    def observe(self, params: Any, reads: ReadFacets, ctx: ObserveContext) -> Observation:
        if self._patient and not self._stalled:
            self._stalled = True  # the first observation is slow: the wait starts late
            ctx.cancellation.wait(timedelta(seconds=STALL_S))  # cooperative: a cancel ends it
        resource = reads.read(ResourceReads)
        seen = resource.observe(SPEC, ctx.lineage, CREATE_EFFECT)
        checked = resource.check("ready", seen.selector_ref) if seen.selector_ref else None
        ready = checked is not None and checked.satisfied and not self._patient
        return Observation(
            present=seen.selector_present or bool(seen.found),
            selector_present=seen.selector_present,
            identity_proven=seen.identity_proven,
            configuration_compatible=seen.configuration_compatible,
            postcondition=CheckResult(ready, None, ""),
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


ENTRY = WorkflowEntry(
    root="app",
    units={
        "app": AllDeclaration(
            unit="app",
            flags=LoopFlags(Compose.ALL, CompletionSource.OBSERVED, Repeat.SAFE),
            children=(
                ChildBinding(unit="quick", params={}, needs=()),
                ChildBinding(unit="slow", params={}, needs=()),
            ),
            concurrency=2,
            budget=timedelta(seconds=ROOT_BUDGET_S),
            identifier_sets={},
            arg_bindings=(),
            env_key_field="env",
        ),
        "quick": Leaf("quick", QUICK_BUDGET_S, 8, patient=False),
        "slow": Leaf("slow", SLOW_BUDGET_S, SLOW_WAIT_S, patient=True),
    },
    deadline=timedelta(seconds=DEADLINE_S),
)


@trestle(deadline=36, env_arg="env")  # deadline: DEADLINE_S (a decorator argument is a literal)
def slice_coop(ctx: Context, env: str = "dev") -> dict[str, str]:
    marker = FakeMarker(ctx.tmp / "markers", "run")
    ports = {ResourceReads: marker, ResourceCreate: marker, ResourceOwned: marker}
    run_tree(ctx, ENTRY, {}, ports=ports)
    return {"fixture": "slice_coop"}
