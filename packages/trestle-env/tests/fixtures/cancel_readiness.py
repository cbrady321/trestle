"""The cancel-during-readiness test plugin (L.RB-10.1; WR-CANCEL-4, WR-OWN-2, B6.1; never a
product plugin, never collected).

DEVIATION (recorded in B-HOST2-RETURN): the published reference tree has no owned process while it
waits for readiness (its backends are containers; a local override replaces a container rather than
running beside one), so this plugin proves the three dispositions on a tree that has all three at
once, built from the reference units themselves:

    cancel_readiness (all-of)
      +-- backend.http_support   the reference Docker unit: a container this run CREATES
      +-- backend.postgres       the reference Postgres unit with its reuse proof: the FOUND,
      |                          pre-started `postgres` container, reused and never touched
      +-- backend.app            the reference service unit over an agent-launched spec: the stdlib
                                 app `tests/fixtures/apps/http_app.py never`, an OWNED
                                 process that listens but never answers ready, so the run
                                 is still waiting for its readiness when the caller cancels

The command is bound in the plugin function from the operator's environment
(`TRESTLE_K10_APP_PORT`, `TRESTLE_K10_APP_LOG`, `TRESTLE_K10_HTTP_APP`), launched by the real local
process port. The composition root is the reference binding (or the `TRESTLE_ENV_PORTS` seam a
twin sets) wrapped by `_local.local_ports`, so the app is routed to the local process port and the
containers to the container adapter.
"""

from __future__ import annotations

import os
import sys
from datetime import timedelta
from typing import Any

from trestle.plugin import Context, trestle
from trestle.workflow import (
    AllDeclaration,
    ChildBinding,
    CompletionSource,
    Compose,
    LoopFlags,
    RealizationKind,
    Repeat,
    WorkflowEntry,
)
from trestle.workflow.loop import run_tree
from trestle.workflow.ports import BoundCommand, Resolved, ResourceSpec

from trestle_env import tree
from trestle_env.plugins import _bind, _local

ROOT = "cancel_readiness"
APP_UNIT = "backend.app"
APP = "app"
PORT_ENV = "TRESTLE_K10_APP_PORT"
LOG_ENV = "TRESTLE_K10_APP_LOG"
APP_PATH_ENV = "TRESTLE_K10_HTTP_APP"  # the absolute path of tests/fixtures/apps/http_app.py


class AppUnit(tree.ServiceUnit):
    """The reference service unit over an agent-launched spec bound at call time."""

    def __init__(self) -> None:
        super().__init__(
            APP_UNIT,
            APP,
            tree.HTTP_SUPPORT_READY,
            ResourceSpec(APP, RealizationKind.AGENT_LAUNCHED_PROJECT, APP, None),
        )

    def bind(self, environ: Any) -> None:
        command = BoundCommand(
            APP,
            (sys.executable, environ[APP_PATH_ENV], "never"),
            {
                "PORT": environ[PORT_ENV],
                "APP_EVENT_LOG": environ[LOG_ENV],
                "PATH": "/usr/bin:/bin",
            },
            Resolved(sys.executable, "3.12", "pin", "adoption"),
            False,
        )
        self._spec = ResourceSpec(APP, RealizationKind.AGENT_LAUNCHED_PROJECT, APP, command)


UNIT = AppUnit()

ENTRY = WorkflowEntry(
    root=ROOT,
    units={
        ROOT: AllDeclaration(
            unit=ROOT,
            flags=LoopFlags(Compose.ALL, CompletionSource.OBSERVED, Repeat.SAFE),
            children=(
                ChildBinding(unit=tree.HTTP_SUPPORT_UNIT, params={}, needs=()),
                ChildBinding(unit=tree.POSTGRES_UNIT, params={}, needs=()),
                ChildBinding(unit=APP_UNIT, params={}, needs=()),
            ),
            concurrency=3,
            budget=timedelta(seconds=tree.ROOT_BUDGET_S),
            identifier_sets={},
            arg_bindings=(),
            env_key_field="env",
        ),
        tree.HTTP_SUPPORT_UNIT: tree.ServiceUnit(
            tree.HTTP_SUPPORT_UNIT, tree.HTTP_SUPPORT_SERVICE, tree.HTTP_SUPPORT_READY
        ),
        tree.POSTGRES_UNIT: tree.ServiceUnit(
            tree.POSTGRES_UNIT,
            tree.POSTGRES_SERVICE,
            tree.POSTGRES_READY,
            reuse=tree.POSTGRES_REUSE,
        ),
        APP_UNIT: UNIT,
    },
    deadline=timedelta(seconds=tree.DEADLINE_S),
)


@trestle(deadline=160, env_arg="env", packages=("trestle_env", "trestle_packs"))
def cancel_readiness(ctx: Context, env: str) -> dict[str, str]:
    """Bring up a created container, a found one and an owned process; the caller cancels."""
    UNIT.bind(os.environ)
    run_tree(ctx, ENTRY, {"env": env}, ports=_local.local_ports(_bind.reference_ports()))
    return {"env": env}
