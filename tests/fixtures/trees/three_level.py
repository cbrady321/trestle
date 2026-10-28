"""A three-level tree (L.TR-0.1; MC-B3-01; the plan's depth bound, WR-UNIT-7 depth invariance).

`app` runs `web` and the group `data`; `data` runs `db` and `cache`. Five vertices, depth 3."""

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
LABEL = {"vertices": 5, "depth": 3, "shared": None, "expect": "valid"}


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


ENTRY = WorkflowEntry(
    root="app",
    units={
        "app": group(
            "app",
            (
                ChildBinding(unit="data", params={}, needs=()),
                ChildBinding(unit="web", params={}, needs=("data",)),
            ),
            budget=240,
        ),
        "data": group(
            "data",
            (
                ChildBinding(unit="db", params={}, needs=()),
                ChildBinding(unit="cache", params={}, needs=("db",)),
            ),
            budget=100,
        ),
        "db": leaf("db"),
        "cache": leaf("cache"),
        "web": leaf("web"),
    },
    deadline=timedelta(seconds=300),
)


@trestle(deadline=300)
def three_level(ctx: Context) -> dict[str, str]:
    return {"fixture": "three_level"}
