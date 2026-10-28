"""The Slice A reference tree with a choice (L.TR-0.1; MC-B3-01; MC-35's `slice_a_tree`).

`stack` runs the choice `db` (a docker service or one started in the IDE), then `api` and `web`,
each needing the one before. Six vertices (the two alternatives count), depth 3; it holds a
`ChoiceNode`, so the run-time enumerator classes it `choice` (L.TR-3.7)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from trestle.plugin import Context, trestle
from trestle.workflow import (
    AllDeclaration,
    Alternative,
    ChildBinding,
    ChoiceDeclaration,
    ChoiceNode,
    CompletionSource,
    Compose,
    LeafDeclaration,
    LoopFlags,
    RealizationKind,
    Repeat,
    Vantage,
    WaitPolicy,
    WorkflowEntry,
)

STRUCTURAL = "structural fixture (MC-B3-01): declaration only, no behaviour"

# MC-B3-01. `vertices` counts the distinct logical nodes the declaration references (an
# alternative of a choice and an unresolvable reference each count once); `depth` is the
# longest containment chain, a leaf being depth 1; `shared` names the node two parents
# reference, or None; `expect` is "valid" or the ground the tree is defective in.
LABEL = {"vertices": 6, "depth": 3, "shared": None, "expect": "valid"}


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


DB = ChoiceNode(
    unit="db",
    flags=LoopFlags(Compose.CHOICE, CompletionSource.OBSERVED, Repeat.SAFE),
    choice=ChoiceDeclaration(
        logical_system="db",
        alternatives=(
            Alternative(
                "db_docker", RealizationKind.DOCKER_SERVICE, frozenset({Vantage.HOST}), None
            ),
            Alternative(
                "db_ide",
                RealizationKind.EXTERNALLY_MANAGED,
                frozenset({Vantage.HOST}),
                "start it in the IDE",
            ),
        ),
        select_arg=None,
        fallback="db_docker",
        readiness="db_ready",
    ),
    budget=timedelta(seconds=100),
)

ENTRY = WorkflowEntry(
    root="stack",
    units={
        "stack": group(
            "stack",
            (
                ChildBinding(unit="db", params={}, needs=()),
                ChildBinding(unit="api", params={}, needs=("db",)),
                ChildBinding(unit="web", params={}, needs=("api",)),
            ),
            budget=300,
            concurrency=1,
        ),
        "db": DB,
        "db_docker": leaf("db_docker", post="db_ready"),
        "db_ide": leaf("db_ide", post="db_ready"),
        "api": leaf("api"),
        "web": leaf("web"),
    },
    deadline=timedelta(seconds=600),
)


@trestle(deadline=600)
def slice_a_tree(ctx: Context) -> dict[str, str]:
    return {"fixture": "slice_a_tree"}
