"""L.NW-2.9 fixture plugin: a run whose body reads through the real container adapter, so the
docker invocation it starts (the `fake_docker.py` shim, by absolute path) is a descendant of the
run and reaches the host only as the adapter's `ExecutionPort` lets it (WR-CANCEL-5, B3-C14).

`shim` is the launcher's absolute path, `endpoint` the endpoint it is bound to (the plugin's
`env_arg`: importing a port module requires one, WR-OWN-8), `reads` how many observations to make.
It returns what each read said, comma-joined: the could-not-observe code, and whether the
selector was present.
A plugin may import neither `trestle.workflow.ports` nor `.values`, and `observe` reads only the
spec's `logical_system` and the lineage's run id and path, so both are plain namespaces.
"""

from __future__ import annotations

from types import SimpleNamespace

from trestle.plugin.surface import Context, trestle

from trestle_packs.container import bind
from trestle_packs.process.command import CommandPort


@trestle(env_arg="endpoint")
def docker_adapter_probe(ctx: Context, shim: str, endpoint: str, reads: int = 1) -> dict[str, str]:
    port = bind(shim, endpoint, CommandPort()).containers
    spec = SimpleNamespace(logical_system="suite-db", entry="suite-entry", command=None)
    lineage = SimpleNamespace(root_run_id="r_probe_0001", path=SimpleNamespace(segments=("probe",)))
    seen = [port.observe(spec, lineage, "up") for _ in range(reads)]  # type: ignore[arg-type]
    return {
        "codes": ",".join(str(s.code) for s in seen),
        "present": ",".join(str(s.selector_present) for s in seen),
    }
