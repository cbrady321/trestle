"""The local-override test plugin (L.RB-8.2; WR-ENV-2, D-11, MC-B-01; never a product plugin, never
collected).

The reference tree (`trestle_env.tree`) publishes no realization choice yet, so this plugin proves
the override contract on a tree that does: the supporting service is a CHOICE between its Docker
node and the catalog's agent-launched override `http_support_local` (`trestle_env.realization
.choice_for`, selected by the request's `overrides`), and the reference Postgres child consumes it
from a CONTAINER:

    local_override (all-of)
      +-- http_support   CHOICE  backend.http_support.docker | http_support_local
      +-- postgres       the reference Postgres child, needs http_support, from a CONTAINER

* `overrides=[http_support_local]` with `services=[http_support]`: the override app runs as a local
  process launched by the toolchain-bound command (`argv[0]` the resolved absolute interpreter,
  recorded as the `override.launch` evidence event), ready when its declared `GET /health` answers;
  the Docker node is never created.
* the operator's project directory absent: the node ends BLOCKED `environment.repository_missing`
  and nothing falls back to Docker.
* `overrides=[http_support_local]` with Postgres in scope: Postgres consumes the override from a
  container, which a local process cannot serve, so admission refuses the request
  `ROUTE_UNSUPPORTED` before a run id (V-7.3).

The composition root is `override_ports`: `_bind.reference_ports` (or the `TRESTLE_ENV_PORTS` seam
the twins set) wrapped by `plugins._local.local_ports`, so both bindings route local nodes to the
real local process port.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from datetime import timedelta
from typing import Any

from trestle.plugin import Context, trestle
from trestle.workflow import (
    AllDeclaration,
    ArgBinding,
    ChildBinding,
    ChoiceNode,
    CompletionSource,
    Compose,
    LoopFlags,
    RealizationKind,
    Repeat,
    Vantage,
    WorkflowEntry,
)
from trestle.workflow.loop import run_tree
from trestle.workflow.ports import ResourceSpec
from trestle.workflow.units import (
    ActContext,
    Blocked,
    EffectFacets,
    ObserveContext,
    ReadFacets,
    Step,
)
from trestle.workflow.values import CheckResult, Observation, Resend, Verdict

from trestle_env import realization, tree
from trestle_env.plugins import _bind, _local
from trestle_env.schema import ENV_ARG, OVERRIDES_ARG, SERVICES_ARG, OverrideId, ServiceId

ROOT = "local_override"
CHOICE = "backend.http_support"
DOCKER_ALTERNATIVE = "backend.http_support.docker"
OVERRIDE = "http_support_local"
LOGICAL = tree.HTTP_SUPPORT_SERVICE
CHOICE_BUDGET_S = 50
ROOT_BUDGET_S = 100
DEADLINE_S = 130

# The request's binding, set by the plugin function before `run_tree` (units are built at import).
RUN: dict[str, Any] = {"bound": None}


class LocalUnit(tree.ServiceUnit):
    """The override alternative: the reference service unit over an agent-launched spec whose
    command the composition root bound from the operator's environment (`RUN["bound"]`). A
    command that could not be bound blocks the node with its code: never a Docker fallback."""

    def __init__(self, unit: str, service: str, readiness: str) -> None:
        super().__init__(
            unit,
            service,
            readiness,
            ResourceSpec(service, RealizationKind.AGENT_LAUNCHED_PROJECT, OVERRIDE, None),
        )
        self._bound_for: Any = None

    def _bind(self) -> _local.OverrideBlocked | None:
        bound = RUN["bound"]
        if isinstance(bound, _local.OverrideBlocked) or bound is None:
            return bound
        if self._bound_for is not bound:
            self._spec = ResourceSpec(
                LOGICAL, RealizationKind.AGENT_LAUNCHED_PROJECT, OVERRIDE, bound
            )
            self._bound_for = bound
        return None

    def observe(self, params: Any, reads: ReadFacets, ctx: ObserveContext) -> Observation:
        if self._bind() is not None or RUN["bound"] is None:
            return Observation(
                present=False,
                selector_present=False,
                identity_proven=False,
                configuration_compatible=False,
                postcondition=CheckResult(False, None, ""),
                preconditions=(),
                currency=(),
                found=(),
                code=None,
                payload=None,
            )
        return super().observe(params, reads, ctx)

    def advance(self, params: Any, state: Verdict, effects: EffectFacets, ctx: ActContext) -> Step:
        blocked = self._bind()
        if blocked is not None:
            return Blocked(blocked.code, blocked.human_action, Resend.SUCCEEDS_AFTER_ACTION)
        command = self._spec.command
        assert command is not None
        ctx.evidence.event(
            "override.launch",
            {"argv0": command.argv[0], "version": command.resolved.reported_version},
        )
        return super().advance(params, state, effects, ctx)


def entry() -> WorkflowEntry:
    docker = tree.ServiceUnit(DOCKER_ALTERNATIVE, LOGICAL, tree.HTTP_SUPPORT_READY)
    postgres = tree.ServiceUnit(
        tree.POSTGRES_UNIT,
        tree.POSTGRES_SERVICE,
        tree.POSTGRES_READY,
        reuse=tree.POSTGRES_REUSE,
    )
    return WorkflowEntry(
        root=ROOT,
        units={
            ROOT: AllDeclaration(
                unit=ROOT,
                flags=LoopFlags(Compose.ALL, CompletionSource.OBSERVED, Repeat.SAFE),
                children=(
                    ChildBinding(unit=CHOICE, params={}, needs=(), name=LOGICAL),
                    ChildBinding(
                        unit=tree.POSTGRES_UNIT,
                        params={},
                        needs=(LOGICAL,),
                        vantage=Vantage.CONTAINER,
                        name=tree.POSTGRES_SERVICE,
                    ),
                ),
                concurrency=tree.CONCURRENCY,
                budget=timedelta(seconds=ROOT_BUDGET_S),
                identifier_sets=tree.identifier_sets(tree.CATALOG),
                arg_bindings=(
                    ArgBinding(SERVICES_ARG, tree.SERVICES_SET, True),
                    ArgBinding(OVERRIDES_ARG, tree.OVERRIDES_SET, False),
                ),
                env_key_field=ENV_ARG,
            ),
            CHOICE: ChoiceNode(
                unit=CHOICE,
                flags=LoopFlags(Compose.CHOICE, CompletionSource.OBSERVED, Repeat.SAFE),
                choice=realization.choice_for(
                    tree.CATALOG, LOGICAL, DOCKER_ALTERNATIVE, tree.HTTP_SUPPORT_READY
                ),
                budget=timedelta(seconds=CHOICE_BUDGET_S),
            ),
            DOCKER_ALTERNATIVE: docker,
            OVERRIDE: LocalUnit(OVERRIDE, LOGICAL, tree.HTTP_SUPPORT_READY),
            tree.POSTGRES_UNIT: postgres,
        },
        deadline=timedelta(seconds=DEADLINE_S),
    )


ENTRY = entry()


def override_ports(environ: Mapping[str, str] | None = None) -> Mapping[type, object]:
    """The reference binding (or the seam's port map) with local nodes routed to the local port."""
    env = os.environ if environ is None else environ
    return _local.local_ports(_bind.reference_ports(env))


@trestle(deadline=130, env_arg="env", packages=("trestle_env", "trestle_packs"))
def local_override(
    ctx: Context,
    env: str,
    services: set[ServiceId] | None = None,
    overrides: set[OverrideId] | None = None,
) -> dict[str, str]:
    """The override tree; `overrides` selects the catalog's agent-launched alternatives."""
    intent: dict[str, Any] = {"env": env}
    for name, chosen in ((SERVICES_ARG, services), (OVERRIDES_ARG, overrides)):
        if chosen is not None:
            intent[name] = sorted(chosen)
    RUN["bound"] = None
    if overrides and OVERRIDE in overrides:
        RUN["bound"] = _local.LocalOverrides(tree.CATALOG).command_for(OVERRIDE)
    run_tree(ctx, ENTRY, intent, ports=override_ports())
    return {"env": env}
