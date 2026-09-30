"""One leaf that declares an environment, run as a root (L.TR-L.6; MC-B3-01 behaviour fixture;
consumed by L.TR-L.6's host re-run of WR-UNIT-4).

The direct call to the worker `lease_root_child` runs as `pair`'s child: a one-vertex root whose
leaf projects the request's `env`, so it takes the environment lease itself at admission. Its
mutation is held open between the files `entered` and `exited` of the run's tmp dir until the test
writes `go` (see `Node`). Its deadline is later than the root's (300 s against 90 s): a request
whose deadline leaves less than its worst case after the holder's is refused
`admission.environment_busy`, so the direct call waits behind the root only with room to spare.
One vertex, depth 1."""

from __future__ import annotations

import time
from datetime import timedelta
from pathlib import Path
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
LABEL = {"vertices": 1, "depth": 1, "shared": None, "expect": "valid"}

CREATE_EFFECT = "up"
STOP_EFFECT = "stop"

HOLD_S = 25  # the longest a mutation stays open with no `go` (a test that never lets go still ends)
POLL_S = 0.05
LEAF_BUDGET_S = 30
ROOT_BUDGET_S = 60
DEADLINE_S = 300  # later than the root's, so it fits behind a root holding the key (SL-8)

_GATE: Path | None = None  # the run's tmp dir, set by the plugin function before the loop runs


class Node:
    """The worker: its one mutation is a marker create held open between `entered` and `exited`
    files in the run's tmp dir. It writes `entered`, waits until the test writes `go`, creates the
    marker (the effect the lane records) and writes `exited`, so a test that reads those files sees
    exactly when this run's mutation was open."""

    def __init__(self, unit: str) -> None:
        self._unit = unit
        self._spec = ResourceSpec(
            unit, RealizationKind.AGENT_LAUNCHED_PROJECT, "lease-direct-child", None
        )

    def declare(self) -> LeafDeclaration:
        return LeafDeclaration(
            unit=self._unit,
            flags=LoopFlags(Compose.LEAF, CompletionSource.OBSERVED, Repeat.SAFE),
            preconditions=(),
            postcondition="ready",
            wait=WaitPolicy(timedelta(seconds=1), 1.0, timedelta(seconds=6)),
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
            env_key_field="env",
        )

    def observe(self, params: Any, reads: ReadFacets, ctx: ObserveContext) -> Observation:
        resource = reads.read(ResourceReads)
        seen = resource.observe(self._spec, ctx.lineage, CREATE_EFFECT)
        checked = resource.check("ready", seen.selector_ref) if seen.selector_ref else None
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
        gate = _GATE
        assert gate is not None
        (gate / "entered").write_text("1", encoding="utf-8")
        end = time.monotonic() + HOLD_S
        while time.monotonic() < end and not (gate / "go").exists():
            if ctx.cancellation.wait(timedelta(seconds=POLL_S)):
                break
        effects.create(ResourceCreate).create(self._spec, CREATE_EFFECT)
        (gate / "exited").write_text("1", encoding="utf-8")
        return Acted()

    def release(self, params: Any, handle: CreatedHandle, effects: Any, ctx: ActContext) -> Step:
        effects.owned(ResourceOwned).stop(handle, STOP_EFFECT)
        return Acted()


ENTRY = WorkflowEntry(
    root="worker",
    units={"worker": Node("worker")},
    deadline=timedelta(seconds=DEADLINE_S),
)


@trestle(deadline=300, env_arg="env")  # deadline: DEADLINE_S (a decorator argument is a literal)
def lease_direct_child(ctx: Context, env: str = "dev") -> dict[str, str]:
    global _GATE
    _GATE = ctx.tmp
    marker = FakeMarker(ctx.tmp / "markers", "run")
    ports = {ResourceReads: marker, ResourceCreate: marker, ResourceOwned: marker}
    run_tree(ctx, ENTRY, {"env": env}, ports=ports)
    return {"env": env}
