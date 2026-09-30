"""Tree variants of the reference environment (L.RB-12.1; WR-UNIT-5 Docker tier; MC-B-01).

A test plugin, published by the RB-12 proof nodes into their MCP host's plugin directory (never a
product plugin, never collected). Its tree puts the reference tree's real container child beside a
second created container and a fake step, so the root's release phase has real containers to give
back in reverse dependency order:

    variants (all-of)
      +-- backend.postgres   the reference Postgres child (`trestle_env.tree.DockerServiceUnit`)
      +-- backend.helper     a second created container (the alpine role), needs backend.postgres
      +-- step.sibling       a fake step (no port, no effect), needs backend.helper

`mode` picks how the run ends:

* `passed` - every child is satisfied (the fake step's postcondition holds when looked at);
* `sibling_failure` - the fake step fails once both containers exist (an ordinary failure);
* `child_exception` - `backend.helper` raises from `observe` once its container exists;
* `cancel` - the fake step holds (inside `advance`) until the root is cancelled from outside;
* `deadline` - the fake step blocks inside one `observe` call past the root's deadline, so the
  deadline's release point stops the run.

On every path both created containers are released at the root's release phase, the helper (the
dependent) before Postgres (reverse dependency order), through the container port's owned stop
(stop, then remove without volumes, V-10.4), and the run's `ArgvRelease` descriptors name them.

The composition root is `variant_ports`: the reference binding's pieces (`_bind`) plus the
helper's definition, or the `TRESTLE_ENV_PORTS` seam (the twins' fake binding) when it is set.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import replace
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
from trestle.workflow.loop import run_tree
from trestle.workflow.units import ActContext, EffectFacets, Failed, ObserveContext, Step
from trestle.workflow.values import CheckResult, Observation, Verdict
from trestle_packs.container import ContainerDefinition, bind
from trestle_packs.process.command import CommandPort

from trestle_env import tree
from trestle_env.plugins import _bind

ROOT = "variants"
POSTGRES = tree.POSTGRES_UNIT
HELPER = "backend.helper"
SIBLING = "step.sibling"
HELPER_SERVICE = "helper"  # the catalog entry and logical system of the second container
HELPER_ROLE = "alpine"
RUNNING = "running"  # the container adapter's built-in check
KEEP_RUNNING = ("sh", "-c", "trap 'exit 0' TERM; while :; do sleep 1; done")
MODES = ("passed", "sibling_failure", "child_exception", "cancel", "deadline")
HOLD_S = 600  # `cancel` / `deadline`: one held call, far past the root's deadline

# The needs chain is postgres -> helper -> step, so the root's budget holds all three and the
# deadline holds the root plus the release slice. The containers' budgets are shorter than the
# reference leaf's (a cached image is ready in seconds), so the `deadline` variant ends in about a
# minute: its step blocks inside one call and the root deadline's release point ends it.
DEADLINE_S = 75
ROOT_BUDGET_S = 60
POSTGRES_BUDGET_S = 30
POSTGRES_WAIT_S = 25
HELPER_BUDGET_S = 12
HELPER_WAIT_S = 8
STEP_BUDGET_S = 8

# The request's mode, set by the plugin function before `run_tree` (units are built at import).
RUN: dict[str, Any] = {"mode": "passed"}


class PostgresUnit(tree.DockerServiceUnit):
    """The reference Postgres child with a shorter wait and budget (same effects and readiness)."""

    def declare(self) -> LeafDeclaration:
        declared = super().declare()
        wait = replace(declared.wait, max_wait=timedelta(seconds=POSTGRES_WAIT_S))
        return replace(declared, wait=wait, budget=timedelta(seconds=POSTGRES_BUDGET_S))


class HelperUnit(tree.DockerServiceUnit):
    """A second created container; in `child_exception` mode it raises once its container
    exists (the root must still release both containers)."""

    def declare(self) -> LeafDeclaration:
        declared = super().declare()
        wait = replace(declared.wait, max_wait=timedelta(seconds=HELPER_WAIT_S))
        return replace(declared, wait=wait, budget=timedelta(seconds=HELPER_BUDGET_S))

    def observe(self, params: Any, reads: Any, ctx: ObserveContext) -> Observation:
        seen = super().observe(params, reads, ctx)
        if RUN["mode"] == "child_exception" and seen.selector_present:
            raise RuntimeError("fixture: the container child raised after its create")
        return seen


class FakeStep:
    """A fake step: no port and no effect. Its postcondition holds as soon as it is looked at,
    except in `sibling_failure` mode (it never does and its act fails, a recorded step) and in the
    two stop modes (it holds until the root stops)."""

    def declare(self) -> LeafDeclaration:
        return LeafDeclaration(
            unit=SIBLING,
            flags=LoopFlags(Compose.LEAF, CompletionSource.OBSERVED, Repeat.SAFE),
            preconditions=(),
            postcondition="done",
            wait=WaitPolicy(timedelta(seconds=0.2), 1.0, timedelta(seconds=10)),
            resource_kind="step",
            may_touch=frozenset(),
            effects=(),
            retryable=frozenset(),
            remedies=(),
            budget=timedelta(seconds=STEP_BUDGET_S),
            max_attempts=1,
        )

    def observe(self, params: Any, reads: Any, ctx: ObserveContext) -> Observation:
        mode = RUN["mode"]
        if mode == "deadline":
            ctx.cancellation.wait(timedelta(seconds=HOLD_S))  # blocked inside one call
        done = mode not in ("sibling_failure", "cancel", "deadline")
        return Observation(
            present=done,
            selector_present=False,
            identity_proven=done,
            configuration_compatible=done,
            postcondition=CheckResult(done, None, ""),
            preconditions=(),
            currency=(),
            found=(),
            code=None,
            payload=None,
        )

    def advance(self, params: Any, state: Verdict, effects: EffectFacets, ctx: ActContext) -> Step:
        if RUN["mode"] == "cancel":
            ctx.cancellation.wait(timedelta(seconds=HOLD_S))  # held until a stop wakes it
            return Failed("fixture.stopped", "the fake step was held until the root stopped")
        return Failed("fixture.sibling_failed", "the fake step failed after both creates")

    def release(self, params: Any, handle: Any, effects: Any, ctx: ActContext) -> Step:
        raise NotImplementedError("the fake step creates nothing")


ENTRY = WorkflowEntry(
    root=ROOT,
    units={
        ROOT: AllDeclaration(
            unit=ROOT,
            flags=LoopFlags(Compose.ALL, CompletionSource.OBSERVED, Repeat.SAFE),
            children=(
                ChildBinding(unit=POSTGRES, params={}, needs=()),
                ChildBinding(unit=HELPER, params={}, needs=(POSTGRES,)),
                ChildBinding(unit=SIBLING, params={}, needs=(HELPER,)),
            ),
            concurrency=3,
            budget=timedelta(seconds=ROOT_BUDGET_S),
            identifier_sets={},
            arg_bindings=(),
            env_key_field="env",
        ),
        POSTGRES: PostgresUnit(POSTGRES, tree.POSTGRES_SERVICE, tree.POSTGRES_READY),
        HELPER: HelperUnit(HELPER, HELPER_SERVICE, RUNNING),
        SIBLING: FakeStep(),
    },
    deadline=timedelta(seconds=DEADLINE_S),
)


def variant_ports(environ: Mapping[str, str] | None = None) -> Mapping[type, object]:
    """The reference binding plus the helper's definition, or the seam's port map."""
    env = os.environ if environ is None else environ
    if env.get(_bind.PORTS_ENV):
        return _bind.reference_ports(env)
    runner = CommandPort()
    definitions = _bind.container_definitions(env)
    definitions[HELPER_SERVICE] = ContainerDefinition(
        image=_bind.image_for(HELPER_ROLE, env), command=KEEP_RUNNING
    )
    bound = bind(
        _bind.docker_path(env),
        env.get(_bind.ENDPOINT_ENV) or None,
        runner,
        definitions=definitions,
        checks=_bind.exec_checks(),
    )
    return bound.as_map()


@trestle(deadline=75, env_arg="env", packages=("trestle_env", "trestle_packs"))
def tree_variants(ctx: Context, env: str, mode: str = "passed") -> dict[str, str]:
    """The variant tree in `mode` (see the module docstring)."""
    RUN["mode"] = mode
    run_tree(ctx, ENTRY, {"env": env}, ports=variant_ports())
    return {"env": env, "mode": mode}
