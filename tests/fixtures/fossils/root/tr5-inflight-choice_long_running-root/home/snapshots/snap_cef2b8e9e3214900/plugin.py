"""A ChoiceNode whose selected alternative runs a long step (L.TR-5.6; MC-B3-01 behaviour fixture;
consumed by L.TR-5.6's host cases and enumerated by the termination suite as a ChoiceNode plan).

`app` runs `pick`, a choice between `steady` and `spare`, both realizations of one logical system.
Nothing of either is up when it runs, so the selection takes the declared fallback `steady`, which
creates its marker (a fake in-run process, so a process the run owns exists) and then polls for a
readiness that never comes: a long step, so a cancel or a deadline finds the selected alternative
mid-step. `spare` is never walked (the selection left it out). Four vertices (the alternatives
count), depth 3."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from trestle_packs.fakes import FakeMarker

from trestle.plugin import Context, trestle
from trestle.workflow import (
    AllDeclaration,
    Alternative,
    ChildBinding,
    ChoiceDeclaration,
    ChoiceNode,
    CompletionSource,
    Compose,
    EffectDeclaration,
    EffectFacetClass,
    LeafDeclaration,
    Lifetime,
    LoopFlags,
    RealizationKind,
    Repeat,
    Vantage,
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

# MC-B3-01. `vertices` counts the distinct logical nodes the declaration references (an
# alternative of a choice and an unresolvable reference each count once); `depth` is the
# longest containment chain, a leaf being depth 1; `shared` names the node two parents
# reference, or None; `expect` is "valid" or the ground the tree is defective in.
LABEL = {"vertices": 4, "depth": 3, "shared": None, "expect": "valid"}

CREATE_EFFECT = "up"
STOP_EFFECT = "stop"

# The tree's worst case (choice 44 s + reserve 10 s in the root) plus the release slice (10 s) is
# 64 s; the deadline leaves 6 s over it so the dispatch check passes on a real run.
LEAF_BUDGET_S = 34
CHOICE_BUDGET_S = 44
ROOT_BUDGET_S = 54
DEADLINE_S = 70


class Waiter:
    """Creates its marker once and polls for a postcondition that never holds (a long step)."""

    def __init__(self, unit: str) -> None:
        self._unit = unit
        # one logical system per node: a marker another node made is not this node's to find
        self._spec = ResourceSpec(
            unit, RealizationKind.AGENT_LAUNCHED_PROJECT, "choice-long-running", None
        )

    def declare(self) -> LeafDeclaration:
        return LeafDeclaration(
            unit=self._unit,
            flags=LoopFlags(Compose.LEAF, CompletionSource.OBSERVED, Repeat.SAFE),
            preconditions=(),
            postcondition="ready",
            wait=WaitPolicy(timedelta(seconds=1), 1.0, timedelta(seconds=30)),
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
            children=(ChildBinding(unit="pick", params={}, needs=()),),
            concurrency=1,
            budget=timedelta(seconds=ROOT_BUDGET_S),
            identifier_sets={},
            arg_bindings=(),
            env_key_field="env",
        ),
        "pick": ChoiceNode(
            unit="pick",
            flags=LoopFlags(Compose.CHOICE, CompletionSource.OBSERVED, Repeat.SAFE),
            choice=ChoiceDeclaration(
                logical_system="long-running",
                alternatives=(
                    Alternative(
                        "steady", RealizationKind.DOCKER_SERVICE, frozenset({Vantage.HOST}), None
                    ),
                    Alternative(
                        "spare",
                        RealizationKind.AGENT_LAUNCHED_PROJECT,
                        frozenset({Vantage.HOST}),
                        None,
                    ),
                ),
                select_arg=None,
                fallback="steady",
                readiness="ready",
            ),
            budget=timedelta(seconds=CHOICE_BUDGET_S),
        ),
        "steady": Waiter("steady"),
        "spare": Waiter("spare"),
    },
    deadline=timedelta(seconds=DEADLINE_S),
)


@trestle(deadline=70, env_arg="env")  # deadline: DEADLINE_S (a decorator argument is a literal)
def choice_long_running(ctx: Context, env: str = "dev") -> dict[str, str]:
    marker = FakeMarker(ctx.tmp / "markers", "run")
    ports = {ResourceReads: marker, ResourceCreate: marker, ResourceOwned: marker}
    run_tree(ctx, ENTRY, {"env": env}, ports=ports)
    return {"env": env}
