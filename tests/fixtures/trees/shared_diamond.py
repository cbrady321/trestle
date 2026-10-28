"""A node shared by two parents (L.TR-0.1; MC-B3-01; WR-UNIT-2, WR-UNIT-3).

`root` runs `left` and `right`; each of them runs the one unit `shared` with the same bound
parameters, so it is one logical node reached at two paths (`left/shared`, `right/shared`).
Four vertices, depth 3."""

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
LABEL = {"vertices": 4, "depth": 3, "shared": "shared", "expect": "valid"}


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


SHARED = ChildBinding(unit="shared", params={"mode": "common"}, needs=())

ENTRY = WorkflowEntry(
    root="diamond",
    units={
        "diamond": group(
            "diamond",
            (
                ChildBinding(unit="left", params={}, needs=()),
                ChildBinding(unit="right", params={}, needs=()),
            ),
            budget=240,
        ),
        "left": group("left", (SHARED,), budget=100, concurrency=1),
        "right": group("right", (SHARED,), budget=100, concurrency=1),
        "shared": leaf("shared"),
    },
    deadline=timedelta(seconds=300),
)


@trestle(deadline=300)
def shared_diamond(ctx: Context) -> dict[str, str]:
    return {"fixture": "shared_diamond"}
