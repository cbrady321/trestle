"""A published workflow whose declared root is an `AllDeclaration` with two children (L.SV-3.5).

A-1 admits one-vertex roots only, so `Admission.admit` refuses this root with
`admission.plan_multi_vertex_unsupported` before any run id exists (TM-B2-1, register entry
`multi-vertex-refusal`, phase `full`). The tree band lifts the refusal for it (L.TR-L.1)."""

from __future__ import annotations

from datetime import timedelta

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


class Leaf:
    def __init__(self, unit: str) -> None:
        self._unit = unit

    def declare(self) -> LeafDeclaration:
        return LeafDeclaration(
            unit=self._unit,
            flags=LoopFlags(Compose.LEAF, CompletionSource.OBSERVED, Repeat.SAFE),
            preconditions=(),
            postcondition="ready",
            wait=WaitPolicy(timedelta(seconds=1), 1.0, timedelta(seconds=30)),
            resource_kind="marker",
            may_touch=frozenset({"marker"}),
            effects=(),
            retryable=frozenset(),
            remedies=(),
            budget=timedelta(seconds=30),
            max_attempts=1,
        )


ROOT = AllDeclaration(
    unit="stack",
    flags=LoopFlags(Compose.ALL, CompletionSource.OBSERVED, Repeat.SAFE),
    children=(
        ChildBinding(unit="db", params={}, needs=()),
        ChildBinding(unit="api", params={}, needs=("db",)),
    ),
    concurrency=2,
    budget=timedelta(seconds=120),
    identifier_sets={},
    arg_bindings=(),
    env_key_field=None,
)

ENTRY = WorkflowEntry(
    root="stack",
    units={"stack": ROOT, "db": Leaf("db"), "api": Leaf("api")},
    deadline=timedelta(seconds=300),
)


@trestle(deadline=300)
def probe_all_root(ctx: Context) -> dict[str, str]:
    return {"root": "all"}
