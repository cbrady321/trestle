"""A three-step workflow with one retry and one remediation (L.TR-0.1; MC-B3-01; WR-TERM-1).

`build` -> `deploy` -> `verify`, each step a leaf that needs the one before. `deploy` retries the
transient code `transient` and remedies `not_started` with a safe start; four vertices, depth 2."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

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
    RemedyDeclaration,
    Repeat,
    WaitPolicy,
    WorkflowEntry,
)

STRUCTURAL = "structural fixture (MC-B3-01): declaration only, no behaviour"

# MC-B3-01. `vertices` counts the distinct logical nodes the declaration references (an
# alternative of a choice and an unresolvable reference each count once); `depth` is the
# longest containment chain, a leaf being depth 1; `shared` names the node two parents
# reference, or None; `expect` is "valid" or the ground the tree is defective in.
LABEL = {"vertices": 4, "depth": 2, "shared": None, "expect": "valid"}


class Unit:
    """A declaration-only work unit: a structural fixture carries no behaviour (MC-B3-01);
    the serial leaf that first needs a behaviour adds it."""

    def __init__(self, declaration: LeafDeclaration) -> None:
        self._declaration = declaration

    def declare(self) -> LeafDeclaration:
        return self._declaration

    def observe(self, params: Any, reads: Any, ctx: Any) -> Any:
        raise NotImplementedError(STRUCTURAL)

    def advance(self, params: Any, state: Any, effects: Any, ctx: Any) -> Any:
        raise NotImplementedError(STRUCTURAL)

    def release(self, params: Any, handle: Any, effects: Any, ctx: Any) -> Any:
        raise NotImplementedError(STRUCTURAL)


def leaf(
    unit: str,
    *,
    pre: tuple[str, ...] = (),
    post: str = "ready",
    budget: int = 30,
    env: str | None = None,
    retryable: frozenset[str] = frozenset(),
    effects: tuple[EffectDeclaration, ...] = (),
    remedies: tuple[RemedyDeclaration, ...] = (),
) -> Unit:
    return Unit(
        LeafDeclaration(
            unit=unit,
            flags=LoopFlags(Compose.LEAF, CompletionSource.OBSERVED, Repeat.SAFE),
            preconditions=pre,
            postcondition=post,
            wait=WaitPolicy(timedelta(seconds=1), 1.0, timedelta(seconds=10)),
            resource_kind="marker",
            may_touch=frozenset({"marker"}),
            effects=effects,
            retryable=retryable,
            remedies=remedies,
            budget=timedelta(seconds=budget),
            max_attempts=1,
            env_key_field=env,
        )
    )


def group(
    unit: str,
    children: tuple[ChildBinding, ...],
    *,
    budget: int = 120,
    concurrency: int = 2,
    env: str | None = None,
) -> AllDeclaration:
    return AllDeclaration(
        unit=unit,
        flags=LoopFlags(Compose.ALL, CompletionSource.OBSERVED, Repeat.SAFE),
        children=children,
        concurrency=concurrency,
        budget=timedelta(seconds=budget),
        identifier_sets={},
        arg_bindings=(),
        env_key_field=env,
    )


RESTART = EffectDeclaration(
    effect="restart",
    facet=EffectFacetClass.SAFE_START,
    verb="start",
    lifetime=Lifetime.DURABLE,
    host_sections=frozenset(),
    release_timeout=None,
)
REMEDY = RemedyDeclaration(
    code="not_started",
    effect="restart",
    attempts=1,
    total=timedelta(seconds=5),
    cooldown=timedelta(seconds=1),
)

ROOT = group(
    "pipeline",
    (
        ChildBinding(unit="build", params={}, needs=()),
        ChildBinding(unit="deploy", params={}, needs=("build",)),
        ChildBinding(unit="verify", params={}, needs=("deploy",)),
    ),
    concurrency=1,
)

ENTRY = WorkflowEntry(
    root="pipeline",
    units={
        "pipeline": ROOT,
        "build": leaf("build"),
        "deploy": leaf(
            "deploy",
            retryable=frozenset({"transient"}),
            effects=(RESTART,),
            remedies=(REMEDY,),
        ),
        "verify": leaf("verify"),
    },
    deadline=timedelta(seconds=300),
)


@trestle(deadline=300)
def three_step_retry_remedy(ctx: Context) -> dict[str, str]:
    return {"fixture": "three_step_retry_remedy"}
