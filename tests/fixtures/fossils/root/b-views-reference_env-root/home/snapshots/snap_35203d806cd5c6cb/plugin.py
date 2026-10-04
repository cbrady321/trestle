"""The reference environment-and-test workflow as one plugin (L.RB-0.3; hld-wr-environment).

One call brings the reference environment up, and (as the tree grows) provisions, tests and tears
it down. This file is the composition root: it binds the tree's ports (`_bind.reference_ports`)
and hands them, with the declared tree, to the loop's one call. Everything else (what the tree
declares, what "ready" means, what may be reused) is `trestle_env` data and the loop's.

The plugin declares `trestle_env` and `trestle_packs` in its metadata, so the snapshot identity
digest covers the domain and adapter code that actually runs (MC-18). The declared deadline is
`trestle_env.tree.DEADLINE_S` (a decorator argument is a literal, so a test pins the two).
"""

from __future__ import annotations

from collections.abc import Mapping

from trestle.plugin import Context, trestle
from trestle.workflow.declarations import JsonValue
from trestle.workflow.loop import run_tree
from trestle_packs.process.local import LocalProcessPort

from trestle_env.plugins._bind import bind_evidence, derive_closure, reference_ports
from trestle_env.plugins._local import LocalOverrides, local_ports
from trestle_env.schema import OverrideId, ServiceId, TestId
from trestle_env.tree import CATALOG, ENTRY


class _Evidence:
    """The run's evidence sink for ports that record identity (`toolchain.task_start`)."""

    def __init__(self, ctx: Context) -> None:
        self._ctx = ctx

    def event(self, kind: str, fields: Mapping[str, JsonValue]) -> None:
        self._ctx.event(kind, **fields)


@trestle(deadline=160, env_arg="env", packages=("trestle_env", "trestle_packs"))
def reference_env(
    ctx: Context,
    env: str,
    services: set[ServiceId] | None = None,
    tests: set[TestId] | None = None,
    overrides: set[OverrideId] | None = None,
) -> dict[str, str]:
    """Bring up the reference environment. `env` names the environment (the Compose project);
    `services`, `tests` and `overrides` select catalog entries by identifier, and nothing else."""
    # the intent is the request as admitted: only the arguments the caller gave
    intent: dict[str, JsonValue] = {"env": env}
    for name, chosen in (("services", services), ("tests", tests), ("overrides", overrides)):
        if chosen is not None:
            intent[name] = sorted(chosen)
    # a local override's process runs on this run's own local port, its command bound for this
    # run (`LocalOverrides`); the port is closed after the loop's release phase, so its endpoint
    # directory never outlives the run
    local = LocalProcessPort()
    try:
        bound = local_ports(
            reference_ports(artifacts=ctx.outputs / "tests"), local, LocalOverrides(CATALOG)
        )
        bind_evidence(bound, _Evidence(ctx))
        derive_closure(bound, services, overrides or ())  # refused before any effect (WR-ENV-1)
        run_tree(ctx, ENTRY, intent, ports=bound)
    finally:
        local.close()
    return {"env": env}
