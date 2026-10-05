"""The second domain-free one-vertex workflow (L.SL-11.1; MC-35 `second_domain_free`): a published
workflow plugin that touches no Docker, no toolchain adapter and no AWS, and is not the spine
fixture: it shares nothing with `spine_leaf` but the public unit-author surface.

Where the spine leaf is an OBSERVED leaf over a fake marker (create, poll, release), this one is a
RECORDED leaf over the fake command port: its one effect is a `verify` run whose recorded result
(passed, with test counts) is the leaf's completion (V-3.1 J-6). No resource is created, so there is
no handle to release and no marker process; the run's whole life is one ticket, one confirmation
and one recorded result. The runtime that walks it is the same one that walks `spine_leaf`, with
no change (`trestle/**` is untouched by the leaf that registers this workflow): that is the claim
the guarantee suites parameterized over MC-35 make.

`env` is the environment argument (`env_arg`, WR-OWN-8): a plugin that imports the ports module
declares one (the D-b rule). The unit is one class over the public surface, with a module-level
`DECLARATION` the registry and the termination sweep read."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from trestle_packs.fakes import BoundCommand, FakeCommand, Resolved, TestCounts, passed_result

from trestle.plugin import Context, trestle
from trestle.workflow import (
    CompletionSource,
    Compose,
    EffectDeclaration,
    EffectFacetClass,
    LeafDeclaration,
    Lifetime,
    LoopFlags,
    Repeat,
    WaitPolicy,
    WorkflowEntry,
)
from trestle.workflow.loop import run_tree
from trestle.workflow.ports import ExecutionPort
from trestle.workflow.units import (
    ActContext,
    Acted,
    EffectFacets,
    ObserveContext,
    ReadFacets,
    Step,
)
from trestle.workflow.values import CheckResult, Observation, Verdict

UNIT = "second_domain_free"
RUN_EFFECT = "run"  # the leaf's one effect: a `verify` command run (an EVENT: never a handle)
TASK = "verify"
COUNTS = TestCounts(passed=4, failed=0, errors=0, skipped=1)
COMMAND = BoundCommand(TASK, ("/bin/true",), {}, Resolved("/bin/true", "1", "p", "a"), True)

DECLARATION = LeafDeclaration(
    unit=UNIT,
    flags=LoopFlags(Compose.LEAF, CompletionSource.RECORDED, Repeat.SAFE),
    preconditions=(),
    postcondition="verified",
    wait=WaitPolicy(timedelta(seconds=0.2), 1.0, timedelta(seconds=5)),
    resource_kind="report",
    may_touch=frozenset({"report"}),
    effects=(
        EffectDeclaration(
            effect=RUN_EFFECT,
            facet=EffectFacetClass.EVENT,
            verb="",
            lifetime=Lifetime.RUN,
            host_sections=frozenset(),
            release_timeout=None,
        ),
    ),
    retryable=frozenset(),
    remedies=(),
    budget=timedelta(seconds=8),
    max_attempts=2,
    env_key_field="env",
)


class SecondDomainFree:
    """Nothing to observe before the run (a RECORDED leaf is joined from its ticket and result);
    advance starts the one `verify` event through its ticketed facet."""

    def declare(self) -> LeafDeclaration:
        return DECLARATION

    def observe(self, params: Any, reads: ReadFacets, ctx: ObserveContext) -> Observation:
        return Observation(
            present=False,
            selector_present=False,
            identity_proven=False,
            configuration_compatible=True,
            postcondition=CheckResult(False, None, ""),
            preconditions=(),
            currency=(),
            found=(),
            code=None,
            payload=None,
        )

    def advance(self, params: Any, state: Verdict, effects: EffectFacets, ctx: ActContext) -> Step:
        effects.event(ExecutionPort).run(
            COMMAND, RUN_EFFECT, ctx.cancellation, ctx.clock.release_point
        )
        return Acted()

    def release(self, params: Any, handle: Any, effects: Any, ctx: ActContext) -> Step:
        raise RuntimeError("a leaf that creates no handle has nothing to release")


ENTRY = WorkflowEntry(root=UNIT, units={UNIT: SecondDomainFree()}, deadline=timedelta(seconds=120))


@trestle(deadline=120, env_arg="env")
def second_domain_free(ctx: Context, env: str = "dev") -> dict[str, str]:
    ports = {ExecutionPort: FakeCommand({TASK: passed_result(COUNTS)})}
    run_tree(ctx, ENTRY, {"env": env}, ports=ports)
    return {"env": env}
