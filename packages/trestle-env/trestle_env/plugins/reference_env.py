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

from trestle.plugin import Context, trestle
from trestle.workflow.declarations import JsonValue
from trestle.workflow.loop import run_tree

from trestle_env.plugins._bind import derive_closure, reference_ports
from trestle_env.schema import OverrideId, ServiceId, TestId
from trestle_env.tree import ENTRY


@trestle(deadline=120, env_arg="env", packages=("trestle_env", "trestle_packs"))
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
    bound = reference_ports()
    derive_closure(bound, services, overrides or ())  # refused before any effect (WR-ENV-1)
    run_tree(ctx, ENTRY, intent, ports=bound)
    return {"env": env}
