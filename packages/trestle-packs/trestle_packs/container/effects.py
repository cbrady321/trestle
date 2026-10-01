"""Real Docker effects (L.NW-2.6; B3-C3, B3-C4, B3-C5, B3-C6, V-10, V-10.4, MC-B-01, X-11).

`ContainerPort` is `ContainerReads` plus the four effect protocols (`ResourceCreate`,
`ResourceOwned`, `ResourceSafeStart`) over the operator's docker CLI. Every invocation goes through
the injected `ExecutionPort` with the bound endpoint (`DockerCli`); no argv carries a volume flag,
`--force` or a pull; every object is named with the run-scoped selector `trwr-<root run_id>-<path>`.

- `release_descriptor(call)` is pure and returns, before any effect and equal across attempts:
  `Durable(owner)` iff `call.lifetime` is `DURABLE`; for `RUN` the V-10 `ArgvRelease` MC-B-01
  pins: the absolute docker path, an exact-name `ps -aq --no-trunc` observe (lists a stopped
  container too, exits 0 whenever the engine answered, so stdout alone decides present or absent),
  `observe_ok_exit = {0}`, a graceful `stop` by exact name, and an `rm` by exact name that removes
  the stopped container and never its volumes, always set. Every argv starts `--host <endpoint>`
  when an endpoint is bound; the timeout is `call.release_timeout` (at most `RELEASE_TIMEOUT_MAX`);
  the whole descriptor fits `ARGV_RELEASE_MAX`. An owned member carries the handle's recorded
  descriptor unchanged; `start` is `Durable(HOST)` for a found shared engine and
  `Durable(ENVIRONMENT)` otherwise.
- `create` runs `docker run -d --pull never --name <selector> ...` for the entry's declared
  `ContainerDefinition` (declared data paths on tmpfs, ports published on loopback only), after
  reading the selector: an existing instance is left alone and returns `APPLIED` with its identity
  (creation is idempotent per selector, B3-C4). `NOT_APPLIED` is returned only when nothing
  changed and the adapter can prove it (the CLI or engine could not be reached, or a failed `run`
  left the selector absent); a call that may have changed something and cannot be confirmed is
  `UNKNOWN`. It returns without waiting for readiness.
- `stop` stops then removes an owned container without its volumes, so `observe` reads the selector
  absent (B3-C5); `restart` and `recreate` repair the same handle, never release it; `start` moves a
  stopped container to running and does nothing else (B3-C6). Only a container named by a run-scoped
  selector is ever stopped, restarted, recreated or removed: a found instance is never touched.

Executor-chosen values (the contract names none): `recreate` re-runs the definition this adapter
made the instance with (remembered per selector; an instance it did not make is `NOT_APPLIED`);
`start` of a found shared engine (`resource_kind == "docker_engine"`) is `NOT_APPLIED` (the adapter
never starts the engine, D-8); the graceful `stop` and `restart` use docker's own grace period.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Final

from trestle.workflow.declarations import CheckRef, Lifetime, RealizationKind
from trestle.workflow.ports import (
    ArgvRelease,
    CatalogEntry,
    Durable,
    DurableOwner,
    EffectCall,
    ExecutionPolicy,
    Helpers,
    ReleaseDescriptor,
    ResourceSpec,
    SelfProvisioning,
    as_descriptor,
)
from trestle.workflow.services import AttemptTicket
from trestle.workflow.values import (
    Confirmation,
    ConfirmationStatus,
    CreatedHandle,
    FoundRef,
    OwnedHandle,
)

from trestle_packs.container.engine import CALL_BOUND, DockerCli
from trestle_packs.container.reads import (
    DOCKER_KIND,
    SELECTOR_PREFIX,
    ContainerReads,
    ExecCheck,
    could_not_observe,
    name_filter,
    not_run,
    observe_argv,
    selector_name,
)

ENGINE_KIND: Final = "docker_engine"  # a found shared engine (start's HOST case)
ARGV_RELEASE_MAX: Final = 4096  # V-13: an ArgvRelease encoded
RELEASE_TIMEOUT_MAX: Final = CALL_BOUND  # the bound when the effect declares no release_timeout
_APPLIED = ConfirmationStatus.APPLIED
_NOT_APPLIED = ConfirmationStatus.NOT_APPLIED
_UNKNOWN = ConfirmationStatus.UNKNOWN


@dataclass(frozen=True, slots=True)
class ContainerDefinition:
    """What a catalog entry runs as: the image (a pinned `<repo>@sha256:<hex>` ref; nothing is ever
    pulled), its command, environment, declared data paths (mounted on tmpfs: no volume is ever
    created), the container ports published on loopback and an optional existing network."""

    image: str
    command: tuple[str, ...] = ()
    environment: Mapping[str, str] = field(default_factory=dict)
    data_paths: tuple[str, ...] = ()
    ports: tuple[int, ...] = ()
    network: str | None = None


def _applied(identity: str) -> Confirmation:
    return Confirmation(_APPLIED, None, identity)


def _not_applied(code: str | None = None) -> Confirmation:
    return Confirmation(_NOT_APPLIED, code, None)


def _unknown(identity: str | None = None) -> Confirmation:
    return Confirmation(_UNKNOWN, None, identity)


class ContainerPort(ContainerReads):
    """`ResourceReads`, `ResourceCreate`, `ResourceOwned` and `ResourceSafeStart` over docker."""

    def __init__(
        self,
        docker: DockerCli,
        definitions: Mapping[CatalogEntry, ContainerDefinition] | None = None,
        checks: Mapping[CheckRef, ExecCheck] | None = None,
    ) -> None:
        super().__init__(docker, checks)
        self._definitions = dict(definitions or {})
        self._made: dict[str, ContainerDefinition] = {}  # selector -> what this adapter ran

    # ------------------------------------------------------------------ ResourceCreate

    def launch_policy(self, spec: ResourceSpec) -> ExecutionPolicy:
        """Pure (B3-C4): no image is ever pulled (`--pull never`) and a container's processes are
        the engine's, never a helper this port started."""
        return ExecutionPolicy(SelfProvisioning.DISABLED_BY_CONFIGURATION, Helpers.PREVENTED, None)

    def release_descriptor(self, call: EffectCall) -> ReleaseDescriptor:
        member = call.member
        if member == "create":
            spec = call.arguments["spec"]
            if not isinstance(spec, ResourceSpec) or spec.realization is not (
                RealizationKind.DOCKER_SERVICE
            ):
                raise ValueError("a container port creates DOCKER_SERVICE resources only (B3-C4)")
            if call.lifetime is Lifetime.DURABLE:
                return Durable(DurableOwner.ENVIRONMENT)
            return self._argv_release(selector_name(call.lineage), call.release_timeout)
        if member == "start":
            target = call.arguments["target"]
            kind = getattr(target, "resource_kind", "")
            return Durable(DurableOwner.HOST if kind == ENGINE_KIND else DurableOwner.ENVIRONMENT)
        target = call.arguments["target"]  # restart, recreate, stop: the handle's own, unchanged
        return as_descriptor(target.release)

    def _argv_release(self, selector: str, release_timeout: timedelta | None) -> ArgvRelease:
        docker = self._docker
        timeout = RELEASE_TIMEOUT_MAX if release_timeout is None else release_timeout
        descriptor = ArgvRelease(
            executable=docker.executable,
            observe_argv=observe_argv(docker, selector),
            observe_ok_exit=frozenset({0}),
            stop_argv=docker.argv("stop", selector),
            timeout=min(timeout, RELEASE_TIMEOUT_MAX),
            remove_argv=docker.argv("rm", selector),
        )
        size = len(descriptor.executable) + sum(
            len(part)
            for argv in (
                descriptor.observe_argv,
                descriptor.stop_argv,
                descriptor.remove_argv or (),
            )
            for part in argv
        )
        if size > ARGV_RELEASE_MAX:
            raise ValueError(f"the release descriptor is {size} bytes, over ARGV_RELEASE_MAX")
        return descriptor

    def create(self, spec: ResourceSpec, ticket: AttemptTicket) -> Confirmation:
        if spec.realization is not RealizationKind.DOCKER_SERVICE:
            raise ValueError("a container port creates DOCKER_SERVICE resources only (B3-C4)")
        definition = self._definitions.get(spec.entry)
        if definition is None:
            raise ValueError(f"no container definition for catalog entry {spec.entry!r}")
        selector = selector_name(ticket.lineage)
        docker = self._docker
        present = self._present(selector, ticket)
        if isinstance(present, Confirmation):
            return present  # could not read the engine: nothing changed
        if present:  # an earlier attempt landed unconfirmed: converge on its instance (B3-C4)
            self._made.setdefault(selector, definition)
            return _applied(selector)
        made = docker.call(self._run_args(selector, definition), ticket)
        if not made.started:
            return _not_applied(not_run(made))
        if made.interrupted is None and made.exit_status == 0:
            self._made[selector] = definition
            return _applied(selector)
        after = self._present(selector, ticket)  # what did the failed or interrupted run leave?
        if isinstance(after, Confirmation) or made.interrupted is not None:
            return _unknown(selector)
        if after:  # created but not started (docker run failed after create): it exists
            self._made[selector] = definition
            return _applied(selector)
        return _not_applied()  # observed absent after the call: nothing changed

    # ------------------------------------------------------------------ ResourceOwned

    def restart(self, target: OwnedHandle, ticket: AttemptTicket) -> Confirmation:
        selector = self._owned(target)
        present = self._present(selector, ticket)
        if isinstance(present, Confirmation):
            return present
        if not present:
            return _not_applied()
        call = self._docker.call(("restart", selector), ticket)
        if call.interrupted is None and call.started and call.exit_status == 0:
            return _applied(selector)
        return _unknown(selector)

    def recreate(self, target: OwnedHandle, ticket: AttemptTicket) -> Confirmation:
        selector = self._owned(target)
        definition = self._made.get(selector)
        if definition is None:
            return _not_applied()  # this adapter did not make it, so it cannot make it again
        present = self._present(selector, ticket)
        if isinstance(present, Confirmation):
            return present
        docker = self._docker
        if present:
            gone = self._remove(selector, ticket)
            if gone is not True:
                return gone
        made = docker.call(self._run_args(selector, definition), ticket)
        if made.started and made.interrupted is None and made.exit_status == 0:
            return _applied(selector)
        return _unknown(selector)

    def stop(self, target: CreatedHandle, ticket: AttemptTicket) -> Confirmation:
        selector = self._owned(target)
        present = self._present(selector, ticket)
        if isinstance(present, Confirmation):
            return present
        if not present:
            return _applied(selector)  # already stopped and removed
        gone = self._remove(selector, ticket)
        return _applied(selector) if gone is True else gone

    def _remove(self, selector: str, ticket: AttemptTicket) -> bool | Confirmation:
        """Stop, then remove by exact name, never a volume (B3-C5). True once observed absent."""
        docker = self._docker
        docker.call(("stop", selector), ticket)
        removed = docker.call(("rm", selector), ticket)
        if removed.interrupted is None and removed.started and removed.exit_status == 0:
            return True
        after = self._present(selector, ticket)  # a failed rm: is it gone anyway?
        if after is False:
            return True
        return _unknown(selector)

    # ------------------------------------------------------------------ ResourceSafeStart

    def start(self, target: FoundRef | OwnedHandle, ticket: AttemptTicket) -> Confirmation:
        """Stopped -> running and nothing else (B3-C6, WR-OWN-10)."""
        if getattr(target, "resource_kind", DOCKER_KIND) != DOCKER_KIND:
            return _not_applied()  # the adapter never starts a shared engine (D-8)
        name = target.selector
        present = self._present(name, ticket)
        if isinstance(present, Confirmation):
            return present
        if not present:
            return _not_applied()
        call = self._docker.call(("start", name), ticket)
        if call.interrupted is None and call.started and call.exit_status == 0:
            return _applied(name)
        return _unknown(name)

    # ------------------------------------------------------------------ internals

    def _owned(self, target: OwnedHandle) -> str:
        selector = target.selector
        if not selector.startswith(SELECTOR_PREFIX):
            raise ValueError(f"{selector!r} is not a run-scoped selector: never touched (B3-C5)")
        return selector

    def _present(self, name: str, ticket: AttemptTicket) -> bool | Confirmation:
        """Whether a container of exactly this name exists, or `NOT_APPLIED(code)` when the engine
        could not be read (so the caller may claim nothing changed)."""
        call = self._docker.call(("ps", "-aq", "--no-trunc", *_name_args(name)), ticket)
        down = could_not_observe(call)
        if down is not None:
            return _not_applied(down)
        return call.output.strip() != ""

    def _run_args(self, selector: str, definition: ContainerDefinition) -> Sequence[str]:
        args: list[str] = ["run", "-d", "--pull", "never", "--name", selector]
        if definition.network is not None:
            args += ["--network", definition.network]
        for path in definition.data_paths:
            args += ["--tmpfs", path]
        for port in definition.ports:
            args += ["--publish", f"127.0.0.1::{port}"]
        for key, value in sorted(definition.environment.items()):
            args += ["-e", f"{key}={value}"]
        return [*args, definition.image, *definition.command]


def _name_args(name: str) -> tuple[str, str]:
    return ("--filter", f"name={name_filter(name)}")
