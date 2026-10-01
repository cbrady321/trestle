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
from trestle.workflow.loop import run_tree

from trestle_env.plugins._bind import reference_ports
from trestle_env.schema import ServiceName
from trestle_env.tree import ENTRY


@trestle(deadline=180, env_arg="env", packages=("trestle_env", "trestle_packs"))
def reference_env(
    ctx: Context, env: str, services: list[ServiceName] | None = None
) -> dict[str, str]:
    """Bring up the reference environment. `env` names the environment (the Compose project);
    `services` selects catalog services by identifier."""
    intent = {"env": env, "services": [str(name) for name in services or ()]}
    run_tree(ctx, ENTRY, intent, ports=reference_ports())
    return {"env": env}
