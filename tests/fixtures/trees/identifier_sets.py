"""Domain identifier sets that filter children (L.TR-0.1; MC-B3-01; WR-PLAN-2, L.TR-1.6).

`only` names services; a value outside the set `services` is an unknown identifier and a listed
value selects that child plus what it needs. Four vertices, depth 2."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from trestle.plugin import Context, trestle
from trestle.workflow import (
    AllDeclaration,
    ArgBinding,
    ChildBinding,
    CompletionSource,
    Compose,
    LeafDeclaration,
    LoopFlags,
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
            effects=(),
            retryable=retryable,
            remedies=(),
            budget=timedelta(seconds=budget),
            max_attempts=1,
            env_key_field=env,
        )
    )


ROOT = AllDeclaration(
    unit="services",
    flags=LoopFlags(Compose.ALL, CompletionSource.OBSERVED, Repeat.SAFE),
    children=(
        ChildBinding(unit="api", params={}, needs=()),
        ChildBinding(unit="web", params={}, needs=("api",)),
        ChildBinding(unit="worker", params={}, needs=()),
    ),
    concurrency=2,
    budget=timedelta(seconds=180),
    identifier_sets={"services": frozenset({"api", "web", "worker"})},
    arg_bindings=(ArgBinding(arg="only", identifier_set="services", filters_children=True),),
    env_key_field=None,
)

ENTRY = WorkflowEntry(
    root="services",
    units={"services": ROOT, "api": leaf("api"), "web": leaf("web"), "worker": leaf("worker")},
    deadline=timedelta(seconds=300),
)


@trestle(deadline=300)
def identifier_sets(ctx: Context, only: str = "") -> dict[str, str]:
    return {"fixture": "identifier_sets"}
