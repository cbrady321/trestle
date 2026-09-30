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
* `TRESTLE_MISE_PATH` - the operator's absolute path of the toolchain manager (`mise`; only by
  absolute path, never on `PATH`); with `TRESTLE_ENV_PROJECTS_DIR` (the directory holding one
  directory per catalog project) it binds the toolchain leg: the resolver and the task runner.
  Without them a request that names a catalog test blocks (nothing is installed, OQ-18) and a
  request that names none is unaffected. `TRESTLE_ENV_ENVELOPE` and `TRESTLE_ENV_DISTRIBUTIONS`
  optionally name the resolver's cache directory and the Gradle distribution store;
* `TRESTLE_ENV_PORTS` - `module:callable` (or `/abs/file.py:callable`), a binding seam for proof
  harnesses: when set, the named callable is given the environment and returns the port map
  INSTEAD of this module's own binding (how a stub twin runs the same tree on a fake engine
  without a second plugin, or a host case plants a wrong readiness password on the real one).

The definitions here only join the tree's declared data (`trestle_env.tree`) to the adapters'
types; they hold no behaviour of their own.
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import shutil
import sys
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from types import ModuleType
from typing import Any, Final

from trestle.workflow import ports
from trestle_packs.container import ContainerDefinition, ExecCheck, bind
from trestle_packs.process.command import CommandPort
from trestle_packs.testrun import PytestJunitRunner
from trestle_packs.toolchain import MiseToolchainResolver
from trestle_packs.toolchain.tasks import ProjectTasks, TaskDeclaration, TaskRunner

from trestle_env import tree
from trestle_env.closure import ClosurePlan, Refused, closure
from trestle_env.plugins._http import HttpReadinessReads
from trestle_env.plugins._tasks import TaskExecution
from trestle_env.stages import closure_failure

DOCKER_PATH_ENV: Final = "TRESTLE_DOCKER_PATH"
ENDPOINT_ENV: Final = "TRESTLE_DOCKER_ENDPOINT"
IMAGE_ENV_PREFIX: Final = "TRESTLE_IMAGE_"
COMPOSE_ENV: Final = "TRESTLE_ENV_COMPOSE_FILE"
PORTS_ENV: Final = "TRESTLE_ENV_PORTS"
MISE_PATH_ENV: Final = "TRESTLE_MISE_PATH"
PROJECTS_DIR_ENV: Final = "TRESTLE_ENV_PROJECTS_DIR"
ENVELOPE_ENV: Final = "TRESTLE_ENV_ENVELOPE"
DISTRIBUTIONS_ENV: Final = "TRESTLE_ENV_DISTRIBUTIONS"
REFERENCE_COMPOSE_PROJECT: Final = "reference"  # the catalog project the Compose file defines

HTTP_SUPPORT_PORT: Final = 80
HTTP_DOCROOT: Final = "/usr/share/nginx/html"
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


def http_support_command() -> tuple[str, ...]:
    """What the supporting container runs: write the declared response at the declared path, then
    serve it. (Derived from the tree's contract, so the two cannot drift apart.)"""
    contract = tree.HTTP_SUPPORT_READINESS
    serve = (
        f'echo -n {contract.body} > {HTTP_DOCROOT}{contract.path} && exec nginx -g "daemon off;"'
    )
    return ("sh", "-c", serve)


def container_definitions(environ: Mapping[str, str]) -> dict[str, ContainerDefinition]:
    """What each catalog entry runs as: the definitions the container adapter creates from."""
    return {
        tree.HTTP_SUPPORT_SERVICE: ContainerDefinition(
            image=image_for(tree.HTTP_SUPPORT_ROLE, environ),
            command=http_support_command(),
            ports=(HTTP_SUPPORT_PORT,),
        ),
        tree.POSTGRES_SERVICE: ContainerDefinition(
            image=image_for(tree.POSTGRES_ROLE, environ),
            environment={
                "POSTGRES_USER": tree.POSTGRES_USER,
                "POSTGRES_DB": tree.POSTGRES_DATABASE,
                "POSTGRES_PASSWORD": tree.POSTGRES_FIXTURE_PASSWORD,
            },
            data_paths=(POSTGRES_DATA,),
            ports=(POSTGRES_PORT,),
        ),
    }


def exec_checks(
    readiness_environment: Mapping[str, str] | None = None,
) -> dict[str, ExecCheck]:
    """The tree's declared exec readiness checks, bound to the adapter's registry.

    `readiness_environment` replaces the environment of the Postgres check (a proof harness
    plants a wrong password here: readiness must then never pass, KDD 2)."""
    checks: dict[str, ExecCheck] = {}
    for name, declared in tree.EXEC_CHECKS.items():
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
    use_seam: bool = True,
    artifacts: Path | None = None,
) -> Mapping[type, object]:
    """The port map for `run_tree(..., ports=...)`: the container ports, the compose resolver and
    the execution port (`execution` defaults to `CommandPort`). `use_seam` False binds this
    module's own ports even when `TRESTLE_ENV_PORTS` names a factory (the factory itself calls it
    that way to wrap the real binding)."""
    env = os.environ if environ is None else environ
    seam = env.get(PORTS_ENV) if use_seam else None
    if seam:
        # a harness's fake binding need not know the toolchain leg: the tree's task nodes run
        # nothing unless the request names a test, and running nothing needs only this port
        mapping = dict(_seam(seam)(env))
        mapping.setdefault(ports.ExecutionPort, TaskExecution(None, None))
        return mapping
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
    resolver, tasks = toolchain_ports(env, runner, artifacts=artifacts)
    if resolver is not None:
        mapping[ports.ToolchainResolver] = resolver
    # the HTTP readiness contracts the tree declares are answered by a decorator over the reads
    mapping[ports.ResourceReads] = HttpReadinessReads(bound.containers, tree.HTTP_READINESS)
    if not compose:
        del mapping[ports.ComposeResolver]  # no definition to derive a closure from: none bound
    mapping[ports.ExecutionPort] = TaskExecution(runner, tasks)
    return mapping


def project_tasks(
    projects_dir: str, *, distribution_store: str | None = None
) -> dict[str, ProjectTasks]:
    """What the task runner is allowed to run: every catalog project's tasks, from the catalog."""
    return {
        str(p.id): ProjectTasks(
            directory=os.path.join(projects_dir, str(p.id)),
            tasks={
                str(t.id): TaskDeclaration(
                    str(t.id), tuple(str(a) for a in t.argv), t.reports_tests
                )
                for t in p.tasks
            },
            environment={},
            distribution_store=distribution_store,
        )
        for p in tree.CATALOG.projects
    }


def toolchain_ports(
    env: Mapping[str, str],
    execution: ports.ExecutionPort,
    *,
    mise: str | None = None,
    project_environment: Mapping[str, Mapping[str, str]] | None = None,
    artifacts: Path | None = None,
) -> tuple[MiseToolchainResolver | None, TaskRunner | None]:
    """The toolchain resolver and the task runner, or `(None, None)` when none is configured.
    `mise` and `project_environment` (a catalog project -> the allowlisted environment that makes
    the toolchain manager answer for it) default to the operator's environment; a proof harness
    names them to bind the mise-shaped stub. A task the catalog marks as a test selector runs
    through the pytest JUnit runner: its counts and failing ids come from the report it writes
    under `artifacts` (kept as run artifacts), never from console text."""
    path = mise if mise is not None else env.get(MISE_PATH_ENV)
    projects_dir = env.get(PROJECTS_DIR_ENV)
    if not path or not projects_dir:
        return None, None
    environment = project_environment or {str(p.id): {} for p in tree.CATALOG.projects}
    resolver = MiseToolchainResolver(
        path, execution, environment, envelope=env.get(ENVELOPE_ENV) or None
    )
    running: ports.ExecutionPort = (
        execution if artifacts is None else PytestJunitRunner(execution, artifacts)
    )
    runner = TaskRunner(
        resolver,
        running,
        project_tasks(projects_dir, distribution_store=env.get(DISTRIBUTIONS_ENV) or None),
    )
    return resolver, runner


def bind_evidence(bound: Mapping[type, object], sink: Any) -> None:
    """Give the run's evidence sink to every bound port that records identity through one."""
    for impl in bound.values():
        binder = getattr(impl, "bind_evidence", None)
        if callable(binder):
            binder(sink)


class ClosureRefusedError(RuntimeError):
    """The Compose closure of the selection could not be derived or names a service the catalog
    does not hold. `code` is the V-11 code exactly as the resolver or `closure()` gave it."""

    def __init__(self, code: str, identifier: str) -> None:
        self.failure = closure_failure(code, identifier)
        super().__init__(self.failure.text())  # names the stage (closure) and the service
        self.code = code
        self.identifier = identifier


def derive_closure(
    bound: Mapping[type, object],
    selected: Iterable[str] | None = None,
    overrides: Iterable[str] = (),
) -> ClosurePlan | None:
    """Derive the Compose closure of the selection BEFORE any effect (WR-ENV-1, AMB-4).

    With a Compose definition bound, the resolver's closure of `selected` (default: every catalog
    service) goes through `closure()`: a service the catalog does not hold, a definition the
    resolver refuses and a selection over the bound are refused here with their V-11 codes, so the
    run ends with no container created. Returns the `ClosurePlan` (the services the selection
    starts, with the realization each runs as), or None when no definition is bound."""
    resolver = bound.get(ports.ComposeResolver)
    if resolver is None:
        return None
    names = (
        frozenset(str(s.id) for s in tree.CATALOG.services)
        if selected is None
        else frozenset(map(str, selected))
    )
    derived = resolver.closure(REFERENCE_COMPOSE_PROJECT, names)  # type: ignore[attr-defined]
    plan = closure(tree.CATALOG, derived, names, overrides)
    if isinstance(plan, Refused):
        raise ClosureRefusedError(plan.code, plan.identifier)
    return plan


def _seam(spec: str) -> Callable[[Mapping[str, str]], Mapping[type, object]]:
    module, _, attr = spec.rpartition(":")
    if not module or not attr:
        raise BindingError(f"{PORTS_ENV} must be module:callable or /path/file.py:callable")
    if module.endswith(".py"):  # a file the harness ships beside its tests: importable nowhere else
        loaded = _load_file(module)
    else:
        loaded = importlib.import_module(module)
    factory: Callable[[Mapping[str, str]], Mapping[type, object]] = getattr(loaded, attr)
    return factory


def _load_file(path: str) -> ModuleType:
    if not os.path.isabs(path) or not os.path.isfile(path):
        raise BindingError(f"{PORTS_ENV}: {path!r} is not an absolute file path")
    name = f"trestle_env_ports_seam_{abs(hash(path))}"
    found = importlib.util.spec_from_file_location(name, path)
    if found is None or found.loader is None:
        raise BindingError(f"{PORTS_ENV}: cannot load {path!r}")
    module = importlib.util.module_from_spec(found)
    sys.modules[name] = module  # a dataclass resolves its module through sys.modules
    found.loader.exec_module(module)
    return module
