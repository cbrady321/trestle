"""The reference composition root's port binding (L.RB-0.3; MC-B-01, C.5 step 4, DIP).

`reference_ports()` is the one place the reference environment names a concrete adapter: it binds
the Docker container adapters and SL-3's `ExecutionPort` (`CommandPort`, the single place a
process is started) into the port map `run_tree` takes. Everything it needs about the machine
comes from the operator's environment, never from a request:

* `TRESTLE_DOCKER_PATH` - the operator's absolute docker path (else the executable `docker` on the
  operator's own `PATH`, resolved to an absolute path here, once; the adapters never search);
* `TRESTLE_DOCKER_ENDPOINT` - the engine endpoint every docker argv is given as `--host` (I-4:
  the `default` context's socket may be absent; `docker_gate` exports the active context's);
* `TRESTLE_IMAGE_<ROLE>` - the digest-pinned image of each MC-B-10 role, exported by
  `docker_gate run`; an image is named by role and never pulled;
* `TRESTLE_ENV_COMPOSE_FILE` - the absolute path of the reference Compose definition the closure
  is derived from (optional until a leaf reads a closure);
* `TRESTLE_ENV_PORTS` - `module:callable`, a binding seam for proof harnesses: when set, the named
  callable is given the environment and returns the port map INSTEAD of this module's own binding
  (how a stub twin runs the same tree on a fake engine without a second plugin).

The definitions here only join the tree's declared data (`trestle_env.tree`) to the adapters'
types; they hold no behaviour of their own.
"""

from __future__ import annotations

import importlib
import os
import shutil
from collections.abc import Callable, Mapping
from typing import Final

from trestle.workflow import ports
from trestle_packs.container import ContainerDefinition, ExecCheck, bind
from trestle_packs.process.command import CommandPort

from trestle_env import tree

DOCKER_PATH_ENV: Final = "TRESTLE_DOCKER_PATH"
ENDPOINT_ENV: Final = "TRESTLE_DOCKER_ENDPOINT"
IMAGE_ENV_PREFIX: Final = "TRESTLE_IMAGE_"
COMPOSE_ENV: Final = "TRESTLE_ENV_COMPOSE_FILE"
PORTS_ENV: Final = "TRESTLE_ENV_PORTS"
REFERENCE_COMPOSE_PROJECT: Final = "reference"  # the catalog project the Compose file defines

POSTGRES_PORT: Final = 5432
POSTGRES_DATA: Final = "/var/lib/postgresql/data"  # tmpfs: no volume is ever created


class BindingError(RuntimeError):
    """The operator's environment cannot bind the reference ports; the message names what is
    missing."""


def docker_path(environ: Mapping[str, str]) -> str:
    """The operator's absolute docker path."""
    named = environ.get(DOCKER_PATH_ENV) or shutil.which("docker", path=environ.get("PATH"))
    if not named or not os.path.isabs(named):
        raise BindingError(f"no absolute docker path: set {DOCKER_PATH_ENV}")
    return named


def image_for(role: str, environ: Mapping[str, str]) -> str:
    """The pinned image of an MC-B-10 role (`<repo>@sha256:<hex>`); never a tag, never pulled."""
    name = f"{IMAGE_ENV_PREFIX}{role.upper()}"
    image = environ.get(name)
    if not image:
        raise BindingError(f"{name} is not set: run through docker_gate, which exports the pins")
    return image


def container_definitions(environ: Mapping[str, str]) -> dict[str, ContainerDefinition]:
    """What each catalog entry runs as: the definitions the container adapter creates from."""
    return {
        tree.POSTGRES_SERVICE: ContainerDefinition(
            image=image_for(tree.POSTGRES_ROLE, environ),
            environment={
                "POSTGRES_USER": tree.POSTGRES_USER,
                "POSTGRES_DB": tree.POSTGRES_DATABASE,
                "POSTGRES_PASSWORD": tree.POSTGRES_FIXTURE_PASSWORD,
            },
            data_paths=(POSTGRES_DATA,),
            ports=(POSTGRES_PORT,),
        )
    }


def exec_checks(
    readiness_environment: Mapping[str, str] | None = None,
) -> dict[str, ExecCheck]:
    """The tree's declared exec readiness checks, bound to the adapter's registry.

    `readiness_environment` replaces the environment of the Postgres check (a proof harness
    plants a wrong password here: readiness must then never pass, KDD 2)."""
    checks: dict[str, ExecCheck] = {}
    for name, declared in tree.READINESS.items():
        environment = declared.environment
        if name == tree.POSTGRES_READY and readiness_environment is not None:
            environment = readiness_environment
        checks[name] = ExecCheck(declared.argv, dict(environment))
    return checks


def reference_ports(
    environ: Mapping[str, str] | None = None,
    *,
    execution: ports.ExecutionPort | None = None,
    readiness_environment: Mapping[str, str] | None = None,
) -> Mapping[type, object]:
    """The port map for `run_tree(..., ports=...)`: the container ports, the compose resolver and
    the execution port (`execution` defaults to `CommandPort`)."""
    env = os.environ if environ is None else environ
    seam = env.get(PORTS_ENV)
    if seam:
        return _seam(seam)(env)
    runner: ports.ExecutionPort = CommandPort() if execution is None else execution
    compose = env.get(COMPOSE_ENV)
    bound = bind(
        docker_path(env),
        env.get(ENDPOINT_ENV) or None,
        runner,
        definitions=container_definitions(env),
        checks=exec_checks(readiness_environment),
        compose_projects={REFERENCE_COMPOSE_PROJECT: compose} if compose else None,
    )
    mapping = bound.as_map()
    mapping[ports.ExecutionPort] = runner
    return mapping


def _seam(spec: str) -> Callable[[Mapping[str, str]], Mapping[type, object]]:
    module, _, attr = spec.partition(":")
    if not module or not attr:
        raise BindingError(f"{PORTS_ENV} must be module:callable, got {spec!r}")
    factory: Callable[[Mapping[str, str]], Mapping[type, object]] = getattr(
        importlib.import_module(module), attr
    )
    return factory
