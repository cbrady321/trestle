"""A composite workflow: three steps over a fake marker, `build` and `lint` together, then
`package`, which needs both. The whole tree is admitted as one run.

It imports the port module, so it names its environment argument (`env_arg="env"`), which is
also the root's lease key (`env_key_field`); a request gives it a value: `args={"env": "dev"}`.
The fake marker comes from `trestle_packs`, which must be installed in the service's environment.
Budgets fit: 30 + 10 <= 60, each leaf 10 <= 30 - 10, the chain build -> package 10 + 10 <= 30."""

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
from trestle.workflow.units import Acted
from trestle.workflow.values import CheckResult, FoundRef, Observation


class Step:
    """One leaf: create a marker, wait until it is ready, release it on the way out."""

    def __init__(self, unit: str) -> None:
        self.unit = unit
        self.spec = ResourceSpec(unit, RealizationKind.AGENT_LAUNCHED_PROJECT, "demo", None)

    def declare(self) -> LeafDeclaration:
        return LeafDeclaration(
            unit=self.unit,
            flags=LoopFlags(Compose.LEAF, CompletionSource.OBSERVED, Repeat.SAFE),
            preconditions=(),
            postcondition="ready",
            wait=WaitPolicy(timedelta(seconds=0.2), 1.0, timedelta(seconds=6)),
            resource_kind="marker",
            may_touch=frozenset({"marker"}),
            effects=(
                EffectDeclaration(
                    "up",
                    EffectFacetClass.CREATE,
                    "",
                    Lifetime.RUN,
                    frozenset(),
                    timedelta(seconds=2),
                ),
                EffectDeclaration(
                    "stop",
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
            budget=timedelta(seconds=10),
            max_attempts=2,
        )

    def observe(self, params: Any, reads: Any, ctx: Any) -> Observation:
        resource = reads.read(ResourceReads)
        seen = resource.observe(self.spec, ctx.lineage, "up")
        target = seen.selector_ref
        checked = resource.check("ready", target) if target is not None else None
        ready = checked is not None and checked.satisfied
        return Observation(
            present=seen.selector_present,
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

    def advance(self, params: Any, state: Any, effects: Any, ctx: Any) -> Acted:
        effects.create(ResourceCreate).create(self.spec, "up")
        return Acted()

    def release(self, params: Any, handle: Any, effects: Any, ctx: Any) -> Acted:
        effects.owned(ResourceOwned).stop(handle, "stop")
        return Acted()


ENTRY = WorkflowEntry(
    root="release",
    units={
        "release": AllDeclaration(
            unit="release",
            flags=LoopFlags(Compose.ALL, CompletionSource.OBSERVED, Repeat.SAFE),
            children=(
                ChildBinding(unit="build", params={}, needs=()),
                ChildBinding(unit="lint", params={}, needs=()),
                ChildBinding(unit="package", params={}, needs=("build", "lint")),
            ),
            concurrency=2,
            budget=timedelta(seconds=30),
            identifier_sets={},
            arg_bindings=(),
            env_key_field="env",
        ),
        "build": Step("build"),
        "lint": Step("lint"),
        "package": Step("package"),
    },
    deadline=timedelta(seconds=60),
)


@trestle(deadline=60, env_arg="env", packages=["trestle_packs"])
def composite_workflow(ctx: Context, env: str = "dev") -> dict[str, str]:
    marker = FakeMarker(ctx.tmp / "markers", "run")
    ports = {ResourceReads: marker, ResourceCreate: marker, ResourceOwned: marker}
    run_tree(ctx, ENTRY, {"env": env}, ports=ports)
    return {"env": env}
