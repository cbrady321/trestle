"""Real Docker adapters for the workflow loop's Container Control family (L.NW-2.3 ...).

`engine` locates the operator's docker CLI and reads the engine's reachability with distinct
stable codes; the read facets, effects and compose resolver follow in their own modules. Adapters
import only the standard library and `trestle.workflow` (BFD-47, N7, C.5 step 4): the types they
return are the workflow package's own, and every docker invocation goes through the injected
`ExecutionPort` (B3-C14). The legacy `trestle_packs.docker` subpackage is untouched (BFD-49).
"""

from trestle_packs.container.engine import (
    ADAPTER_CODES,
    DOCKER_CLI_MISSING,
    DOCKER_ENGINE_UNREACHABLE,
    CliMissing,
    DockerCli,
    Reachable,
    Unreachable,
    engine_state,
    locate_cli,
)

__all__ = [
    "ADAPTER_CODES",
    "DOCKER_CLI_MISSING",
    "DOCKER_ENGINE_UNREACHABLE",
    "CliMissing",
    "DockerCli",
    "Reachable",
    "Unreachable",
    "engine_state",
    "locate_cli",
]
