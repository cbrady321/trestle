"""A tree with one live-state condition per case (L.TR-5.2; MC-B3-01 behaviour fixture; consumed by
L.TR-5.3's host case and enumerated by the termination suite as a ChoiceNode plan).

`system` runs five children: `definition` (a gate: it only reads the platform's live definition),
the choice `data` and its dependent `client` (needs `data`; creating it needs a pinned tool), and
the choice `edge` and its dependent `gateway` (needs `edge`, consumes it from a container). The
`case` argument injects exactly one live-state condition; every other node converges (`healthy`
injects none):

* `absent_em_instance`: no data instance is up, so the fallback `managed_data` (an externally
  managed realization) is selected and its `advance` can only ask for the human action, `Blocked`
  `REALIZATION_ABSENT` (V-7.4); `client` never starts;
* `drifted_definition`: the live definition names a node the declaration does not contain, so the
  gate is not satisfied and the whole root stops `DECLARATION_STALE` before any effect (B1-E7);
* `infeasible_route`: an externally managed `edge` instance is up, so it is selected, but it does
  not declare the container vantage `gateway` consumes it from: `gateway` stops `BLOCKED`
  `ROUTE_UNSUPPORTED` before the first ticket in the root (V-7.3);
* `missing_toolchain`: creating `client` is `NOT_APPLIED` with `TOOLCHAIN_MISSING` (the pinned tool
  is not installed and Trestle installs none; OPEN-MISE-HOST), so `client` is `BLOCKED` with the
  tool named, before any effect.

Ten vertices (the alternatives count), depth 3. The behaviours use `trestle_packs.fakes`'s marker
(one logical system per node, so a marker another node made is not this node's to find)."""

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
from trestle.workflow.units import (
    ActContext,
    Acted,
    Blocked,
    EffectFacets,
    Failed,
    ObserveContext,
    ReadFacets,
    Step,
)
from trestle.workflow.values import (
    CheckResult,
    Confirmation,
    ConfirmationStatus,
    CreatedHandle,
    FoundRef,
    Observation,
    Resend,
    Verdict,
)

# MC-B3-01. `vertices` counts the distinct logical nodes the declaration references (an
# alternative of a choice and an unresolvable reference each count once); `depth` is the
# longest containment chain, a leaf being depth 1; `shared` names the node two parents
# reference, or None; `expect` is "valid" or the ground the tree is defective in.
LABEL = {"vertices": 10, "depth": 3, "shared": None, "expect": "valid"}

CASES = (
    "healthy",
    "absent_em_instance",
    "drifted_definition",
    "infeasible_route",
    "missing_toolchain",
)

CREATE_EFFECT = "up"
STOP_EFFECT = "stop"
TOOL = "python 3.12 (pinned)"  # what `client` needs and the `missing_toolchain` case lacks
ABSENT_CODE = "execution.realization_absent"  # V-11 REALIZATION_ABSENT (a unit's Blocked, V-7.4)
ABSENT_ACTION = "Start the managed data service, then re-send."

LEAF_BUDGET_S = 10
CHOICE_BUDGET_S = 25
ROOT_BUDGET_S = 90
DEADLINE_S = 150


class Node:
    """One leaf. `role` is `gate` (reads the live definition, no effects), `worker` (observe the
    node's marker, create it once, release it on the way out) or `managed` (an externally managed
    realization: it only observes an instance somebody else runs, and its `advance` can only
    ask for the declared human action, V-7.4)."""

    def __init__(self, unit: str, role: str) -> None:
        self._unit = unit
        self._role = role
        realization = (
            RealizationKind.EXTERNALLY_MANAGED
            if role == "managed"
            else RealizationKind.AGENT_LAUNCHED_PROJECT
        )
        # one logical system per node: a marker another node made is not this node's to find
        self._spec = ResourceSpec(unit, realization, "live-state", None)

    def declare(self) -> LeafDeclaration:
        effects = ()
        if self._role == "worker":
            effects = (
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
            )
        return LeafDeclaration(
            unit=self._unit,
            flags=LoopFlags(Compose.LEAF, CompletionSource.OBSERVED, Repeat.SAFE),
            preconditions=(),
            postcondition="ready",
            wait=WaitPolicy(timedelta(seconds=1), 1.0, timedelta(seconds=6)),
            resource_kind="marker",
            may_touch=frozenset({"marker"}),
            effects=effects,
            retryable=frozenset(),
            remedies=(),
            budget=timedelta(seconds=LEAF_BUDGET_S),
            max_attempts=1,
        )

    def observe(self, params: Any, reads: ReadFacets, ctx: ObserveContext) -> Observation:
        if self._role == "gate":  # the live definition, read only: does it name a foreign node?
            intact = params.get("case") != "drifted_definition"
            return Observation(
                present=True,
                selector_present=True,
                identity_proven=True,
                configuration_compatible=True,
                postcondition=CheckResult(intact, None, "" if intact else "names a foreign node"),
                preconditions=(),
                currency=(),
                found=(),
                code=None,
                payload=None,
            )
        resource = reads.read(ResourceReads)
        seen = resource.observe(self._spec, ctx.lineage, CREATE_EFFECT)
        target = seen.selector_ref or (seen.found[0] if seen.found else None)
        checked = resource.check("ready", target) if target is not None else None
        ready = checked is not None and checked.satisfied
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
        if self._role == "gate":
            return Failed("fixture.gate", "a gate has nothing to advance")
        if self._role == "managed":
            return Blocked(
                "execution.realization_absent", ABSENT_ACTION, Resend.SUCCEEDS_AFTER_ACTION
            )
        effects.create(ResourceCreate).create(self._spec, CREATE_EFFECT)
        return Acted()

    def release(self, params: Any, handle: CreatedHandle, effects: Any, ctx: ActContext) -> Step:
        effects.owned(ResourceOwned).stop(handle, STOP_EFFECT)
        return Acted()


def _choice(
    unit: str,
    managed: str,
    other: str,
    other_kind: RealizationKind,
    other_reach: set[Vantage],
    fallback: str,
) -> ChoiceNode:
    """A choice between an externally managed realization (host only, preferred when it is up)
    and `other`; `fallback` is the one selected when neither is up."""
    return ChoiceNode(
        unit=unit,
        flags=LoopFlags(Compose.CHOICE, CompletionSource.OBSERVED, Repeat.SAFE),
        choice=ChoiceDeclaration(
            logical_system=unit,
            alternatives=(
                Alternative(
                    managed,
                    RealizationKind.EXTERNALLY_MANAGED,
                    frozenset({Vantage.HOST}),
                    ABSENT_ACTION,
                ),
                Alternative(other, other_kind, frozenset(other_reach), None),
            ),
            select_arg=None,
            fallback=fallback,
            readiness="ready",
        ),
        budget=timedelta(seconds=CHOICE_BUDGET_S),
    )


ENTRY = WorkflowEntry(
    root="system",
    units={
        "system": AllDeclaration(
            unit="system",
            flags=LoopFlags(Compose.ALL, CompletionSource.OBSERVED, Repeat.SAFE),
            children=(
                ChildBinding(unit="definition", params={"case": "case"}, needs=()),
                ChildBinding(unit="data", params={}, needs=()),
                ChildBinding(unit="client", params={}, needs=("data",)),
                ChildBinding(unit="edge", params={}, needs=()),
                ChildBinding(unit="gateway", params={}, needs=("edge",), vantage=Vantage.CONTAINER),
            ),
            concurrency=3,
            budget=timedelta(seconds=ROOT_BUDGET_S),
            identifier_sets={},
            arg_bindings=(),
            env_key_field="env",
            gates=("definition",),
        ),
        "definition": Node("definition", "gate"),
        "data": _choice(
            "data",
            "managed_data",
            "local_data",
            RealizationKind.AGENT_LAUNCHED_PROJECT,
            {Vantage.HOST},
            "managed_data",
        ),
        "managed_data": Node("managed_data", "managed"),
        "local_data": Node("local_data", "worker"),
        "client": Node("client", "worker"),
        "edge": _choice(
            "edge",
            "managed_edge",
            "docker_edge",
            RealizationKind.DOCKER_SERVICE,
            {Vantage.HOST, Vantage.CONTAINER},
            "docker_edge",
        ),
        "managed_edge": Node("managed_edge", "managed"),
        "docker_edge": Node("docker_edge", "worker"),
        "gateway": Node("gateway", "worker"),
    },
    deadline=timedelta(seconds=DEADLINE_S),
)


class ToolchainCreate:
    """`ResourceCreate` over a marker: creating the node named `refused` is `NOT_APPLIED` with the
    stable `TOOLCHAIN_MISSING` code and the pinned tool as its identity (V-11.2, J-5a); nothing
    is started. Every other create is the marker's."""

    def __init__(self, marker: FakeMarker, refused: str | None) -> None:
        self._marker = marker
        self._refused = refused

    def launch_policy(self, spec: Any) -> Any:
        return self._marker.launch_policy(spec)

    def release_descriptor(self, call: Any) -> Any:
        return self._marker.release_descriptor(call)

    def create(self, spec: Any, ticket: Any) -> Any:
        if self._refused is not None and ticket.lineage.path.segments == (self._refused,):
            return Confirmation(ConfirmationStatus.NOT_APPLIED, "execution.toolchain_missing", TOOL)
        return self._marker.create(spec, ticket)


def prepare(marker: FakeMarker, case: str) -> dict[type, object]:
    """The machine the `case` describes and the ports over it: an externally managed instance of
    `data` is up in every case but `absent_em_instance`, one of `edge` only in `infeasible_route`.
    Returns the port map `run_tree` takes."""
    assert case in CASES, case
    if case != "absent_em_instance":
        marker.plant_found("managed_data", "managed-data")
    if case == "infeasible_route":
        marker.plant_found("managed_edge", "managed-edge")
    create = ToolchainCreate(marker, "client" if case == "missing_toolchain" else None)
    return {ResourceReads: marker, ResourceCreate: create, ResourceOwned: marker}


@trestle(deadline=150, env_arg="env")  # deadline: DEADLINE_S (a decorator argument is a literal)
def live_state(ctx: Context, env: str = "dev", case: str = "healthy") -> dict[str, str]:
    marker = FakeMarker(ctx.tmp / "markers", "run")
    run_tree(ctx, ENTRY, {"env": env, "case": case}, ports=prepare(marker, case))
    return {"env": env, "case": case}
