"""The owned-restart test plugin (L.RB-8.3; B7.1, WR-ENV-7, WR-ENV-10, WR-REMEDY-4; never a product
plugin, never collected).

The owned-restart remedy is the product's: `tree.LocalServiceUnit` (the reference tree's local
realization, V03 stage 10 decision C) declares one effect `restart` (OWNED) and one remedy,
`execution.postcondition_timeout` repaired by `restart` of the node's own handle, once. A node that
is not owned (found, never created by this run) is never granted it (J-20), and the port refuses a
selector it does not hold with no signal. `OwnedRestartUnit` here is that product unit over a
test-bound spec (the stdlib app), the PROC twin of the reference-tree proof.

    healthy_machine (all-of), the same composition as `cancel_readiness`, with the app restartable:
      +-- backend.http_support   the reference Docker unit: a container this run CREATES  (started)
      +-- backend.postgres       the reference Postgres unit with its reuse proof: the FOUND,
      |                          pre-started `postgres` container                         (reused)
      +-- backend.app            `OwnedRestartUnit` over `tests/fixtures/apps/http_app.py`
                                 `ready-after N`, an OWNED process the test kills while the run
                                 waits for its readiness                                (repaired)

The command is bound in the plugin function from the operator's environment
(`TRESTLE_B7_APP_PORT`, `TRESTLE_B7_APP_LOG`, `TRESTLE_B7_HTTP_APP`), launched by the real local
process port. The composition root is the reference binding (or the `TRESTLE_ENV_PORTS` seam a
twin sets) wrapped by `_local.local_ports`.
"""

from __future__ import annotations

import os
import sys
from datetime import timedelta
from typing import Any, Final

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

ROOT: Final = "healthy_machine"
APP_UNIT: Final = "backend.app"
APP: Final = "app"
RESTART: Final = tree.RESTART
# `trestle.workflow.codes.POSTCONDITION_TIMEOUT`: a plugin may import only `trestle.workflow`'s
# public surface (R-PLUG-6), so the spelling is repeated here and pinned by the PROC test
POSTCONDITION_TIMEOUT: Final = "execution.postcondition_timeout"
PORT_ENV: Final = "TRESTLE_B7_APP_PORT"
LOG_ENV: Final = "TRESTLE_B7_APP_LOG"
APP_PATH_ENV: Final = "TRESTLE_B7_HTTP_APP"  # the absolute path of tests/fixtures/apps/http_app.py
REFUSALS: Final = "5"  # the app answers /health 503 this many times, per process, before 200
WAIT_S: Final = tree.LOCAL_READY_WAIT_S  # the readiness wait: when a killed app is repaired
REMEDY_TOTAL_S: Final = tree.RESTART_TOTAL_S  # the remedy's own bound (V-14)
REMEDY_ATTEMPTS: Final = 1


class OwnedRestartUnit(tree.LocalServiceUnit):
    """The product local leaf, with its owned-restart remedy, over a spec the test binds."""

    def __init__(
        self, unit: str, service: str, readiness: str, spec: ResourceSpec | None = None
    ) -> None:
        super().__init__(unit, service, readiness)
        if spec is not None:
            self._spec = spec


def app_command(port: str, log: str, http_app: str, refusals: str = REFUSALS) -> BoundCommand:
    """The bound command of the stdlib app: `argv[0]` the resolved absolute interpreter."""
    return BoundCommand(
        APP,
        (sys.executable, http_app, "ready-after", refusals),
        {"PORT": port, "APP_EVENT_LOG": log, "PATH": "/usr/bin:/bin"},
        Resolved(sys.executable, "3.12", "pin", "adoption"),
        False,
    )


def app_spec(command: BoundCommand | None) -> ResourceSpec:
    return ResourceSpec(APP, RealizationKind.AGENT_LAUNCHED_PROJECT, APP, command)


class AppUnit(OwnedRestartUnit):
    """The restartable app unit whose command is bound from the operator's environment at call
    time (units are built at import)."""

    def __init__(self) -> None:
        super().__init__(APP_UNIT, APP, tree.HTTP_SUPPORT_READY, app_spec(None))

    def bind(self, environ: Any) -> None:
        self._spec = app_spec(
            app_command(environ[PORT_ENV], environ[LOG_ENV], environ[APP_PATH_ENV])
        )


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
def healthy_machine(ctx: Context, env: str) -> dict[str, str]:
    """Bring up a created container, reuse a found one and run a restartable owned process."""
    UNIT.bind(os.environ)
    run_tree(ctx, ENTRY, {"env": env}, ports=_local.local_ports(_bind.reference_ports()))
    return {"env": env}
