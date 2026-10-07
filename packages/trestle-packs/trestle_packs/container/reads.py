"""Real Docker read facets (L.NW-2.5; B3-C1, B3-C2, V-3.8, V-4.6, V-5.1, MC-B-01).

`ContainerReads` implements `ResourceReads` over the operator's docker CLI, every invocation an
`ExecutionPort.run` (`DockerCli`, so stdin is closed and every process is attributable to the run):

- `observe` reports `selector_present` only for the instance this root's run-scoped selector names
  (`trwr-<root run_id>-<path>`, matched by an exact-name `ps -aq` filter, the same argv the release
  descriptor's `observe_argv` uses) and sets `selector_ref` exactly then; any other instance, a
  container named `spec.logical_system`, appears only in `found`. Ownership is never inferred from
  labels (BFD-43) and identity is proven by the declared proof, this root's exact name, never by a
  name or a port being occupied (WR-OWN-7). A missing CLI or an unreachable engine is V-3.8's
  could-not-observe shape (`DOCKER_CLI_MISSING` / `DOCKER_ENGINE_UNREACHABLE`), never a partial or
  an absent result.
- `check` runs a declared check: the built-in `running`, or an exec check registered by id (a
  command run inside the container by `docker exec`; exit 0 satisfies it).
- `endpoint` gives a dependant the address as seen from its vantage: `HOST`, the published port on
  loopback (`docker port`); `CONTAINER`, the container's name and its private port. A target that is
  not a Docker container (a local process) is `RouteRefused(ROUTE_UNSUPPORTED)` with a human
  action, never a best-effort address (B3-C2, B3-E2).

The class has no mutating member and issues only read verbs (`ps`, `port`, `exec` of a declared
check): `exec` runs a check the caller declared and is the one read verb that starts a process in
the container (V-5.1's permitted set is the operation's `INCIDENTAL_WRITES`, empty here).

Executor-chosen values (the contract names none): a found instance is a container whose exact name
is `spec.logical_system`; `identity_proven` and `configuration_compatible` are `selector_present`;
the first published TCP port is the endpoint's port; the exec check runs with the environment its
`ExecCheck` names, passed as `-e K=V` (the environment of the docker call itself is empty).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Final

from trestle.workflow.declarations import CheckRef, EffectId, Vantage
from trestle.workflow.ports import Endpoint, ResourceObservation, ResourceSpec, RouteRefused
from trestle.workflow.values import (
    CheckResult,
    CreatedHandle,
    FoundRef,
    Lineage,
    OwnedHandle,
    SelectorRef,
)

from trestle_packs.container.engine import (
    DOCKER_CLI_MISSING,
    DOCKER_ENGINE_UNREACHABLE,
    TEXT_MAX,
    Call,
    DockerCli,
)

SELECTOR_PREFIX: Final = "trwr-"  # MC-B-01: the run-scoped selector `trwr-<root run_id>-<path>`
DOCKER_KIND: Final = "docker_container"  # the resource kind of a container FoundRef
ROUTE_UNSUPPORTED: Final = "admission.route_unsupported"
FOUND_MAX: Final = 16  # V-13: the first FOUND_MAX kept
RUNNING: Final = "running"  # the built-in check id
_UNSAFE = re.compile(r"[^a-z0-9_.-]")
_CONTAINER_NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")
_PORT_LINE = re.compile(r"^(\d+)/tcp\s*->\s*\S*?:(\d+)\s*$")


def selector_name(lineage: Lineage) -> str:
    """`trwr-<root run_id>-<path>`: `path` is the node path's segments joined by `.`, lowercase
    `[a-z0-9_.-]` (any other character becomes `_`). Depends only on the lineage (V-10.1)."""
    path = _UNSAFE.sub("_", ".".join(lineage.path.segments).lower())
    return f"{SELECTOR_PREFIX}{lineage.root_run_id}-{path}"


def name_filter(name: str) -> str:
    """`--filter name=` value matching exactly one container name: `^/<name>$` with `.` escaped."""
    return "^/" + name.replace(".", r"\.") + "$"


def observe_argv(docker: DockerCli, selector: str) -> tuple[str, ...]:
    """The discriminating presence check MC-B-01 pins (also the release descriptor's
    `observe_argv`): lists a stopped container too and exits 0 whenever the engine answered, so
    stdout alone decides present or absent."""
    return docker.argv("ps", "-aq", "--no-trunc", "--filter", f"name={name_filter(selector)}")


def _observe_args(name: str) -> tuple[str, ...]:
    return ("ps", "-aq", "--no-trunc", "--filter", f"name={name_filter(name)}")


def not_run(call: Call) -> str | None:
    """The code when docker did not run to its end: the executable could not be started, or the
    port ended it (cancel or deadline). None when it ran, whatever its exit status."""
    if not call.started:
        return DOCKER_CLI_MISSING
    return call.interrupted


def could_not_observe(call: Call) -> str | None:
    """V-3.8: the code when the call could not read the engine; None when the engine answered
    (exit 0, V-10.4's `observe_ok_exit`)."""
    code = not_run(call)
    if code is None and call.exit_status != 0:
        return DOCKER_ENGINE_UNREACHABLE
    return code


def target_name(target: CreatedHandle | OwnedHandle | FoundRef | SelectorRef) -> str:
    return target.selector


def _detail(text: str) -> str:
    return text.strip().replace("\n", " ").encode("utf-8")[:TEXT_MAX].decode("utf-8", "ignore")


@dataclass(frozen=True, slots=True)
class ExecCheck:
    """A declared check run inside a container: `docker exec [-e K=V ...] <name> <argv>`."""

    argv: tuple[str, ...]
    environment: Mapping[str, str] = field(default_factory=dict)


class ContainerReads:
    """`ResourceReads` over the docker CLI (no mutating member; read verbs only)."""

    def __init__(
        self, docker: DockerCli, checks: Mapping[CheckRef, ExecCheck] | None = None
    ) -> None:
        self._docker = docker
        self._checks = dict(checks or {})

    # ------------------------------------------------------------------ observe

    def observe(
        self, spec: ResourceSpec, lineage: Lineage, effect: EffectId | None
    ) -> ResourceObservation:
        docker = self._docker
        selector = None if effect is None else selector_name(lineage)
        present = False
        if selector is not None:
            call = docker.call(_observe_args(selector))
            down = could_not_observe(call)
            if down is not None:
                return ResourceObservation(False, None, False, False, (), (), down)
            present = call.output.strip() != ""
        found: tuple[FoundRef, ...] = ()
        system = spec.logical_system
        if system != selector and _CONTAINER_NAME.match(system):
            call = docker.call(_observe_args(system))
            down = could_not_observe(call)
            if down is not None:
                return ResourceObservation(False, None, False, False, (), (), down)
            if call.output.strip():
                found = (FoundRef(DOCKER_KIND, system, datetime.now(UTC)),)
        ref = (
            SelectorRef(lineage, effect, selector, datetime.now(UTC))
            if present and selector is not None and effect is not None
            else None
        )
        return ResourceObservation(present, ref, present, present, (), found[:FOUND_MAX], None)

    # ------------------------------------------------------------------ check

    def check(
        self, check: CheckRef, target: CreatedHandle | OwnedHandle | FoundRef | SelectorRef
    ) -> CheckResult:
        name = target_name(target)
        if getattr(
            target, "resource_kind", DOCKER_KIND
        ) != DOCKER_KIND or not _CONTAINER_NAME.match(name):
            return CheckResult(False, None, _detail(f"{name} is not a docker container"))
        docker = self._docker
        running = docker.call(("ps", "-q", "--no-trunc", "--filter", f"name={name_filter(name)}"))
        down = could_not_observe(running)
        if down is not None:
            return CheckResult(False, down, "the docker engine could not be read")
        if running.output.strip() == "":
            return CheckResult(False, None, _detail(f"{name} is not running"))
        if check == RUNNING:
            return CheckResult(True, None, _detail(f"{name} is running"))
        declared = self._checks.get(check)
        if declared is None:
            return CheckResult(False, None, _detail(f"{check} is not a check this adapter knows"))
        env = [part for k, v in sorted(declared.environment.items()) for part in ("-e", f"{k}={v}")]
        call = docker.call(("exec", *env, name, *declared.argv))
        down = not_run(call)
        if down is not None:
            return CheckResult(False, down, "the check could not be run")
        if call.exit_status == 0:
            return CheckResult(True, None, _detail(f"{check} holds"))
        return CheckResult(False, None, _detail(f"{check} not yet: {call.output}"))

    # ------------------------------------------------------------------ endpoint

    def endpoint(
        self, target: CreatedHandle | OwnedHandle | FoundRef | SelectorRef, vantage: Vantage
    ) -> Endpoint | RouteRefused:
        name = target_name(target)
        if getattr(
            target, "resource_kind", DOCKER_KIND
        ) != DOCKER_KIND or not _CONTAINER_NAME.match(name):
            return RouteRefused(
                ROUTE_UNSUPPORTED,
                "Select a Docker realization of this service: only a Docker container has an "
                "address a dependant can be routed to (a local process has none).",
            )
        docker = self._docker
        call = docker.call(("port", name))
        down = not_run(call)
        if down is not None:
            return RouteRefused(
                down, "Install the Docker CLI or start the Docker engine, then retry."
            )
        mapping = self._first_tcp_port(call.output) if call.exit_status == 0 else None
        if mapping is None:
            probe = docker.call(_observe_args(name))
            down = could_not_observe(probe)
            if down is not None:
                return RouteRefused(down, "Start the Docker engine, then retry.")
            return RouteRefused(
                ROUTE_UNSUPPORTED,
                f"Start {name} with a published port: it is not running or publishes none.",
            )
        private, published = mapping
        if Vantage(vantage) is Vantage.CONTAINER:
            return Endpoint("tcp", name, private)
        return Endpoint("tcp", "127.0.0.1", published)

    @staticmethod
    def _first_tcp_port(output: str) -> tuple[int, int] | None:
        for line in output.splitlines():
            match = _PORT_LINE.match(line.strip())
            if match:
                return int(match.group(1)), int(match.group(2))
        return None
