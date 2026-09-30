"""A root that is a choice between two fake realizations (L.TR-0.1; MC-B3-01; WR-UNIT-8, TR-5).

`pick` chooses `fake_a` or `fake_b`; the shared readiness check is `fake_ready`. Three vertices
(the alternatives count), depth 2. A `ChoiceNode` root: the temporary refusal keeps it until
L.TR-5.3."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from trestle.plugin import Context, trestle
from trestle.workflow import (
    Alternative,
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
LABEL = {"vertices": 3, "depth": 2, "shared": None, "expect": "valid"}


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


ROOT = ChoiceNode(
    unit="pick",
    flags=LoopFlags(Compose.CHOICE, CompletionSource.OBSERVED, Repeat.SAFE),
    choice=ChoiceDeclaration(
        logical_system="fake",
        alternatives=(
            Alternative("fake_a", RealizationKind.DOCKER_SERVICE, frozenset({Vantage.HOST}), None),
            Alternative(
                "fake_b",
                RealizationKind.EXTERNALLY_MANAGED,
                frozenset({Vantage.HOST}),
                "start it by hand",
            ),
        ),
        select_arg=None,
        fallback="fake_a",
        readiness="fake_ready",
    ),
    budget=timedelta(seconds=120),
)

ENTRY = WorkflowEntry(
    root="pick",
    units={
        "pick": ROOT,
        "fake_a": leaf("fake_a", post="fake_ready"),
        "fake_b": leaf("fake_b", post="fake_ready"),
    },
    deadline=timedelta(seconds=300),
)


@trestle(deadline=300)
def choice_fake(ctx: Context) -> dict[str, str]:
    return {"fixture": "choice_fake"}
