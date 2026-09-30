"""A published workflow whose declared root is a `ChoiceNode` (L.SV-3.5).

`Admission.admit` refuses this root with `admission.plan_multi_vertex_unsupported` before any run
id exists in phase `full` and in phase `choice-only` of the register entry `multi-vertex-refusal`
(TM-B2-1); only the tree band's TR-5 lifts it."""

from __future__ import annotations

from datetime import timedelta

from trestle.plugin import Context, trestle
from trestle.workflow import (
    Alternative,
    ChoiceDeclaration,
    ChoiceNode,
    CompletionSource,
    Compose,
    LoopFlags,
    RealizationKind,
    Repeat,
    Vantage,
    WorkflowEntry,
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

ENTRY = WorkflowEntry(root="pick", units={"pick": ROOT}, deadline=timedelta(seconds=300))


@trestle(deadline=300)
def probe_choice_root(ctx: Context) -> dict[str, str]:
    return {"root": "choice"}
