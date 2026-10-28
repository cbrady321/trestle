"""Two parallel branches and a barrier (L.TR-0.1; MC-B3-01): `join` needs `left` and `right`.

Four vertices, depth 2. The barrier node starts only after both branches are terminal
(WR-PLAN-10)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from trestle.plugin import Context, trestle
from trestle.workflow import (
    AllDeclaration,
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


ROOT = group(
    "barrier",
    (
        ChildBinding(unit="left", params={}, needs=()),
        ChildBinding(unit="right", params={}, needs=()),
        ChildBinding(unit="join", params={}, needs=("left", "right")),
    ),
)

ENTRY = WorkflowEntry(
    root="barrier",
    units={"barrier": ROOT, "left": leaf("left"), "right": leaf("right"), "join": leaf("join")},
    deadline=timedelta(seconds=300),
)


@trestle(deadline=300)
def two_branch_barrier(ctx: Context) -> dict[str, str]:
    return {"fixture": "two_branch_barrier"}
