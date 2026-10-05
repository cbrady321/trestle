"""The reference plugin over the synthetic 500-service catalog (L.RB-1.3; WR-ENV-12).

Copied into an MCP host's plugin directory by `unit/test_scale_catalog.py`. It is
`plugins/reference_env.py` with one difference: its tree is `trestle_env.tree.ENTRY` with the
root's identifier sets drawn from `gen_catalog.generate(500, SEED)`, so a request may name any of
the 500 generated services and admission binds them exactly as it binds the reference catalog's.
The port map is the composition root's (`TRESTLE_ENV_PORTS` names the fake binding in the test).
The plugin process imports this checkout's packages and `packages/trestle-env/tests` (on the
`PYTHONPATH` the harness sets), where `fixtures.scale.gen_catalog` lives.
"""

from __future__ import annotations

import dataclasses

from fixtures.scale import gen_catalog
from trestle.plugin import Context, trestle
from trestle.workflow.declarations import JsonValue
from trestle.workflow.loop import run_tree

from trestle_env import tree as reference_tree
from trestle_env.catalog import Catalog
from trestle_env.plugins._bind import reference_ports
from trestle_env.schema import OverrideId, ServiceId, TestId

SIZE = 500
SEED = 20260930

_CATALOG = Catalog.from_data(gen_catalog.generate(SIZE, SEED).catalog)
_ROOT = reference_tree.ENTRY.units[reference_tree.ROOT_UNIT]
ENTRY = dataclasses.replace(
    reference_tree.ENTRY,
    units={
        **reference_tree.ENTRY.units,
        reference_tree.ROOT_UNIT: dataclasses.replace(
            _ROOT, identifier_sets=reference_tree.identifier_sets(_CATALOG)
        ),
    },
)


@trestle(deadline=180, env_arg="env", packages=("trestle_env", "trestle_packs"))
def scale_env(
    ctx: Context,
    env: str,
    services: set[ServiceId] | None = None,
    tests: set[TestId] | None = None,
    overrides: set[OverrideId] | None = None,
) -> dict[str, str]:
    """The reference environment, admitted against the synthetic 500-service catalog."""
    intent: dict[str, JsonValue] = {"env": env}
    for name, chosen in (("services", services), ("tests", tests), ("overrides", overrides)):
        if chosen is not None:
            intent[name] = sorted(chosen)
    run_tree(ctx, ENTRY, intent, ports=reference_ports())
    return {"env": env}
