"""Real Docker adapters for the workflow loop's Container Control family (L.NW-2.3 - L.NW-2.7).

`engine` locates the operator's docker CLI, reads the engine's reachability with distinct stable
codes and is the one place `docker` is started (through the injected `ExecutionPort`); `reads` is
`ResourceReads`; `effects` adds `ResourceCreate`, `ResourceOwned` and `ResourceSafeStart`.
Adapters import only the standard library and `trestle.workflow` (BFD-47, N7, C.5 step 4): the
types they return are the workflow package's own. The legacy `trestle_packs.docker` subpackage is
untouched (BFD-49).

`bind(docker_path, endpoint, execution)` (MC-B-01) returns the port set the loop's facet binders
take: one composite adapter serves `ResourceReads`, `ResourceCreate`, `ResourceOwned` and
`ResourceSafeStart` (`PortSet.as_map()`), and the compose resolver joins it when L.NW-2.7 lands
(`PortSet.compose` is `None` until then).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from trestle.workflow import ports
from trestle.workflow.declarations import CheckRef
from trestle.workflow.ports import CatalogEntry, ExecutionPort
from trestle.workflow.values import CancelSignal

from trestle_packs.container.effects import ContainerDefinition, ContainerPort
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
from trestle_packs.container.reads import ContainerReads, ExecCheck


@dataclass(frozen=True, slots=True)
class PortSet:
    """What `bind` returns: the container adapter and, from L.NW-2.7, the compose resolver."""

    containers: ContainerPort
    compose: ports.ComposeResolver | None = None

    def as_map(self) -> dict[type, object]:
        """Protocol type -> the one implementation, the map `FacetContext.ports` takes."""
        bound: dict[type, object] = {
            ports.ResourceReads: self.containers,
            ports.ResourceCreate: self.containers,
            ports.ResourceOwned: self.containers,
            ports.ResourceSafeStart: self.containers,
        }
        if self.compose is not None:
            bound[ports.ComposeResolver] = self.compose
        return bound


def bind(
    docker_path: str | os.PathLike[str],
    endpoint: str | None,
    execution: ExecutionPort,
    *,
    definitions: Mapping[CatalogEntry, ContainerDefinition] | None = None,
    checks: Mapping[CheckRef, ExecCheck] | None = None,
    cancel: CancelSignal | None = None,
) -> PortSet:
    """Bind the container adapter to an absolute docker path, an endpoint (`--host`, I-4; `None`
    binds none) and the injected execution port. `definitions` maps a catalog entry to what it
    runs, `checks` a declared check id to its `docker exec` command, `cancel` the root's signal
    (default: never requested)."""
    docker = DockerCli(docker_path, endpoint, execution, cancel)
    return PortSet(ContainerPort(docker, definitions, checks))


__all__ = [
    "ADAPTER_CODES",
    "DOCKER_CLI_MISSING",
    "DOCKER_ENGINE_UNREACHABLE",
    "CliMissing",
    "ContainerDefinition",
    "ContainerPort",
    "ContainerReads",
    "DockerCli",
    "ExecCheck",
    "PortSet",
    "Reachable",
    "Unreachable",
    "bind",
    "engine_state",
    "locate_cli",
]
