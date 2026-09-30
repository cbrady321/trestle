"""A published workflow whose declared root is a `ChoiceNode` (L.SV-3.5).

`Admission.admit` refuses this root with `admission.plan_multi_vertex_unsupported` before any run
id exists in phase `full` and in phase `choice-only` of the register entry `multi-vertex-refusal`
(TM-B2-1); only the tree band's TR-5 lifts it.

Both alternatives are units of the entry (L.TR-0.4: a descendant no unit supplies is refused at
publication as `publication.unit_unresolved`, and the probe needs this root to publish)."""

from __future__ import annotations

from datetime import timedelta

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


class Leaf:
    def __init__(self, unit: str) -> None:
        self._unit = unit

    def declare(self) -> LeafDeclaration:
        return LeafDeclaration(
            unit=self._unit,
            flags=LoopFlags(Compose.LEAF, CompletionSource.OBSERVED, Repeat.SAFE),
            preconditions=(),
            postcondition="db_ready",
            wait=WaitPolicy(timedelta(seconds=1), 1.0, timedelta(seconds=30)),
            resource_kind="marker",
            may_touch=frozenset({"marker"}),
            effects=(),
            retryable=frozenset(),
            remedies=(),
            budget=timedelta(seconds=30),
            max_attempts=1,
        )


ROOT = ChoiceNode(
    unit="pick",
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
    budget=timedelta(seconds=120),
)

ENTRY = WorkflowEntry(
    root="pick",
    units={"pick": ROOT, "db_docker": Leaf("db_docker"), "db_ide": Leaf("db_ide")},
    deadline=timedelta(seconds=300),
)


@trestle(deadline=300)
def probe_choice_root(ctx: Context) -> dict[str, str]:
    return {"root": "choice"}
