"""The local-override composition helper (L.RB-8.2; MC-B-01, B3-C4, B3-C14, B3-C21, WR-ENV-2).

A local override replaces a service's Docker node with an agent-launched project (the realization
space `trestle_env.realization` declares). This module is the one place that binds what running
it needs to concrete adapters, next to `_bind` (the container binding):

* `LocalOverrides(catalog, environ)` turns an override identifier into a `BoundCommand` or into
  the reason it cannot start. The project's directory is the operator's (`TRESTLE_ENV_PROJECT_DIRS`,
  a JSON object project id -> absolute directory): a directory that is absent is
  `environment.repository_missing` (a unit's `Blocked` step, never a fallback to Docker, WR-ENV-2);
  the pin is resolved by the toolchain port (`trestle_packs.toolchain`, the operator's mise at
  `TRESTLE_MISE_PATH` with the environment `TRESTLE_TOOLCHAIN_ENV` names, a JSON object): argv[0] is
  the resolved absolute interpreter, never a `PATH` lookup, and a pin that is not satisfied is the
  toolchain's own `TOOLCHAIN_MISSING`. The launch command gets the one `PORT` it listens on;
* `RealizationRouter(container, local)` is the one `ResourceReads` + `ResourceCreate` +
  `ResourceOwned` a tree binds when some of its nodes are agent-launched and some are Docker: each
  call goes to the port of the realization it is about (a spec's kind; a handle's or reference's
  own run-scoped selector), so a Docker node's container adapter never sees a local process and the
  local port never sees a container;
* `local_ports(base, ...)` wraps a port map (the real binding or a twin's seam) with that router,
  the local process port answering the tree's HTTP readiness contracts over loopback like the
  container port does.

Nothing here selects a realization: the choice is admission's and the loop's.
"""

from __future__ import annotations

import json
import os
import socket
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from trestle.workflow import ports
from trestle.workflow.declarations import RealizationKind
from trestle.workflow.ports import BoundCommand, Unresolved
from trestle.workflow.values import FoundRef
from trestle_packs.process.command import CommandPort
from trestle_packs.process.local import LocalProcessPort
from trestle_packs.toolchain import MiseToolchainResolver
from trestle_packs.toolchain.tasks import ProjectTasks, TaskDeclaration, TaskRunner

from trestle_env import tree
from trestle_env.catalog import Catalog
from trestle_env.codes import ENVIRONMENT_REPOSITORY_MISSING
from trestle_env.plugins._http import HttpReadinessReads

MISE_PATH_ENV: Final = "TRESTLE_MISE_PATH"
TOOLCHAIN_ENV_ENV: Final = "TRESTLE_TOOLCHAIN_ENV"
PROJECT_DIRS_ENV: Final = "TRESTLE_ENV_PROJECT_DIRS"
PORT_VAR: Final = "PORT"  # the environment name the local process port reads the endpoint from
LOCAL_SELECTOR_PREFIX: Final = "proc-"  # `trestle_packs.process.local.run_scoped_selector`
LOCAL_FOUND_KIND: Final = "local_process"
LOCAL_ALIVE: Final = "ready"  # the local process port's own liveness check
CONTAINER_ALIVE: Final = "running"


@dataclass(frozen=True)
class OverrideBlocked:
    """An override that cannot start: the stable code and the human action a `Blocked` step
    carries."""

    code: str
    human_action: str


def _json_object(environ: Mapping[str, str], name: str) -> dict[str, str]:
    raw = environ.get(name)
    if not raw:
        return {}
    loaded = json.loads(raw)
    if not isinstance(loaded, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in loaded.items()
    ):
        raise ValueError(f"{name} must be a JSON object of strings")
    return loaded


def free_port() -> int:
    """A loopback port nothing listens on now (the launch command's `PORT`)."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class LocalOverrides:
    """Binds the launch command of an override from the operator's environment."""

    def __init__(
        self,
        catalog: Catalog,
        environ: Mapping[str, str] | None = None,
        execution: ports.ExecutionPort | None = None,
    ) -> None:
        self.catalog = catalog
        self.environ = os.environ if environ is None else environ
        self.execution: ports.ExecutionPort = CommandPort() if execution is None else execution

    def command_for(
        self, override_id: str, port: int | None = None
    ) -> BoundCommand | OverrideBlocked:
        override = self.catalog.override(override_id)
        project = None if override is None else self.catalog.project(str(override.project))
        if override is None or project is None:
            raise ValueError(f"{override_id!r} is not a catalog override")
        directory = _json_object(self.environ, PROJECT_DIRS_ENV).get(str(project.id))
        if not directory or not os.path.isdir(directory):
            return OverrideBlocked(
                ENVIRONMENT_REPOSITORY_MISSING,
                f"Check out the repository of project {project.id} at the directory the "
                f"operator configured ({PROJECT_DIRS_ENV}), then re-send: {override.id} runs "
                "from it and never falls back to Docker.",
            )
        mise = self.environ.get(MISE_PATH_ENV)
        if not mise or not os.path.isabs(mise):
            return OverrideBlocked(
                "execution.toolchain_missing",
                f"Name the operator's mise by absolute path in {MISE_PATH_ENV}, then re-send.",
            )
        resolver = MiseToolchainResolver(
            mise, self.execution, {str(project.id): _json_object(self.environ, TOOLCHAIN_ENV_ENV)}
        )
        tasks = {
            str(task.id): TaskDeclaration(str(task.id), tuple(str(a) for a in task.argv))
            for task in project.tasks
        }
        runner = TaskRunner(
            resolver,
            self.execution,
            {
                str(project.id): ProjectTasks(
                    directory, tasks, {PORT_VAR: str(free_port() if port is None else port)}
                )
            },
        )
        bound = runner.bind(str(project.id), str(override.task))
        if isinstance(bound, Unresolved):
            return OverrideBlocked(bound.code, bound.human_action)
        return bound


# -- routing


def _is_local(target: Any) -> bool:
    if isinstance(target, FoundRef):
        return str(target.resource_kind) == LOCAL_FOUND_KIND
    return str(getattr(target, "selector", "")).startswith(LOCAL_SELECTOR_PREFIX)


def _is_local_spec(spec: Any) -> bool:
    kind = getattr(spec.realization, "value", spec.realization)
    return bool(kind == RealizationKind.AGENT_LAUNCHED_PROJECT.value)


class RealizationRouter:
    """`ResourceReads`, `ResourceCreate` and `ResourceOwned` over two ports, by realization."""

    def __init__(
        self,
        container_reads: Any,
        container_create: Any,
        container_owned: Any,
        local_reads: Any,
        local: Any,
    ) -> None:
        self._container_reads = container_reads
        self._container_create = container_create
        self._container_owned = container_owned
        self._local_reads = local_reads
        self._local = local

    # reads
    def observe(self, spec: Any, lineage: Any, effect: Any) -> Any:
        port = self._local_reads if _is_local_spec(spec) else self._container_reads
        return port.observe(spec, lineage, effect)

    def check(self, check: Any, target: Any) -> Any:
        port = self._local_reads if _is_local(target) else self._container_reads
        return port.check(check, target)

    def endpoint(self, target: Any, vantage: Any) -> Any:
        port = self._local_reads if _is_local(target) else self._container_reads
        return port.endpoint(target, vantage)

    # create
    def launch_policy(self, spec: Any) -> Any:
        return (self._local if _is_local_spec(spec) else self._container_create).launch_policy(spec)

    def release_descriptor(self, call: Any) -> Any:
        arguments = call.arguments
        local = (
            _is_local_spec(arguments["spec"])
            if "spec" in arguments
            else _is_local(arguments["target"])
        )
        member = call.member
        if local:
            return self._local.release_descriptor(call)
        owner = self._container_create if member == "create" else self._container_owned
        return owner.release_descriptor(call)

    def create(self, spec: Any, ticket: Any) -> Any:
        return (self._local if _is_local_spec(spec) else self._container_create).create(
            spec, ticket
        )

    # owned
    def restart(self, target: Any, ticket: Any) -> Any:
        return (self._local if _is_local(target) else self._container_owned).restart(target, ticket)

    def recreate(self, target: Any, ticket: Any) -> Any:
        return (self._local if _is_local(target) else self._container_owned).recreate(
            target, ticket
        )

    def stop(self, target: Any, ticket: Any) -> Any:
        return (self._local if _is_local(target) else self._container_owned).stop(target, ticket)


def local_ports(
    base: Mapping[type, object], local: LocalProcessPort | None = None
) -> Mapping[type, object]:
    """`base` (a tree's port map: the real binding or a twin's seam) with the resource ports
    replaced by a `RealizationRouter` over it and a local process port; every other port (the
    compose resolver, the execution port, the safe-start port) is `base`'s own."""
    port = LocalProcessPort() if local is None else local
    router = RealizationRouter(
        base[ports.ResourceReads],
        base[ports.ResourceCreate],
        base[ports.ResourceOwned],
        HttpReadinessReads(port, tree.HTTP_READINESS, alive=LOCAL_ALIVE),
        port,
    )
    return {
        **base,
        ports.ResourceReads: router,
        ports.ResourceCreate: router,
        ports.ResourceOwned: router,
    }
