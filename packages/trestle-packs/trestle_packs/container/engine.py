"""Docker CLI locator, engine reachability and the one way an adapter runs `docker` (L.NW-2.3;
B3-C1, B3-C14, B3-E1, V-3.8, MC-B-01, MC-B-12).

`locate_cli(operator_path)` resolves the operator's absolute path to the `docker` executable and
NEVER searches `PATH` (WR-ENV-15 in spirit: an adapter runs the executable the operator named). A
missing CLI and an unreachable engine are two values with two stable codes, `DOCKER_CLI_MISSING`
and `DOCKER_ENGINE_UNREACHABLE`, returned in V-3.8's could-not-observe shape and never raised
(`WR-VERIFY-3`): the caller reads `.code`.

`DockerCli` is the single place an adapter starts `docker`: every invocation is an
`ExecutionPort.run` of a `BoundCommand` whose `argv[0]` is the absolute docker path, `--host
<endpoint>` first when an endpoint is bound (I-4: the `default` context's socket may be absent),
an environment built from empty (as the host sweep runs a descriptor's argv, B2-C9), stdin closed
and every process attributable to the run (the port's contract, B3-C14, WR-CANCEL-5). An adapter
starts no process of its own.

The stable codes are spelled here as `trestle.common.plan.vocabulary` spells them (the adapter
imports only stdlib and `trestle.workflow`, which carries no copy of the adapter half); the SA-03
drift test for Slice B reads both.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

from trestle.workflow.declarations import EffectFacetClass, Lifetime, Repeat
from trestle.workflow.ports import (
    BoundCommand,
    ExecutionClass,
    ExecutionPort,
    InRunGroup,
    Resolved,
)
from trestle.workflow.services import AttemptTicket
from trestle.workflow.values import (
    CancelSignal,
    ConfirmationStatus,
    Lineage,
    NodePath,
    StopCause,
)

# MC-B-12, adapter half: each NAME is the V-11 name, each value `<origin>.<snake>` (DM-16).
DOCKER_CLI_MISSING: Final = "adapter.docker_cli_missing"
DOCKER_ENGINE_UNREACHABLE: Final = "adapter.docker_engine_unreachable"
TOOLCHAIN_MISSING: Final = "execution.toolchain_missing"
TOOLCHAIN_INTERFACE_DRIFT: Final = "adapter.toolchain_interface_drift"
COMPOSE_DEFINITION_INVALID: Final = "adapter.compose_definition_invalid"
CREDENTIAL_INTERACTIVE: Final = "execution.credential_interactive"
GRANT_ISSUER_UNREACHABLE: Final = "adapter.grant_issuer_unreachable"
PROVISION_STORE_UNREADABLE: Final = "adapter.provision_store_unreadable"
HOST_SCOPE_UNREADABLE: Final = "adapter.host_scope_unreadable"

ADAPTER_CODES: Final = frozenset(
    {
        DOCKER_CLI_MISSING,
        DOCKER_ENGINE_UNREACHABLE,
        TOOLCHAIN_MISSING,
        TOOLCHAIN_INTERFACE_DRIFT,
        COMPOSE_DEFINITION_INVALID,
        CREDENTIAL_INTERACTIVE,
        GRANT_ISSUER_UNREACHABLE,
        PROVISION_STORE_UNREADABLE,
        HOST_SCOPE_UNREADABLE,
    }
)

# Executor-chosen (the contract names none): how long one docker invocation may run before the
# port ends it at `until` (EXECUTION_DEADLINE). A read or an effect that outlives it is a
# could-not-observe / UNKNOWN, never a hang.
CALL_BOUND: Final = timedelta(seconds=60)
# The console tail the port returns as `excerpt` is what an adapter reads of a command's output
# (B3-C14 keeps at most TEXT_MAX of it); every format an adapter asks docker for fits in it.
TEXT_MAX: Final = 512

_READ_TICKET: Final = AttemptTicket(
    lineage=Lineage("", NodePath(())),
    effect="",
    facet=EffectFacetClass.EVENT,
    attempt=1,
    repeat=Repeat.SAFE,
    lifetime=Lifetime.RUN,
    release=InRunGroup(),
    remedy=None,
)  # a read has no attempt ticket (V-5.1); a real ExecutionPort takes the parameter and ignores it


@dataclass(frozen=True, slots=True)
class CliMissing:
    """No docker executable at the operator's path (V-3.8 could-not-observe shape)."""

    path: str
    reason: str
    code: str = DOCKER_CLI_MISSING


@dataclass(frozen=True, slots=True)
class Reachable:
    server_version: str


@dataclass(frozen=True, slots=True)
class Unreachable:
    """The engine did not answer at the bound endpoint (V-3.8 could-not-observe shape)."""

    detail: str
    code: str = DOCKER_ENGINE_UNREACHABLE


def locate_cli(operator_path: str | os.PathLike[str] | None) -> Path | CliMissing:
    """The operator-named docker executable as an absolute `Path`, or `CliMissing`.

    Never searches `PATH` and never resolves a relative name against the working directory: a path
    that is not absolute, not a regular file or not executable is `CliMissing` (with the reason)."""
    if operator_path is None or str(operator_path) == "":
        return CliMissing("", "no docker executable path was supplied")
    text = os.fspath(operator_path)
    if not os.path.isabs(text):
        return CliMissing(text, "the docker path must be absolute (the search path is never used)")
    path = Path(os.path.normpath(text))
    if not path.is_file():
        return CliMissing(str(path), "no such executable file")
    if not os.access(path, os.X_OK):
        return CliMissing(str(path), "the file is not executable")
    return path


class _NeverCancel:
    """The cancel signal of a call whose caller supplied none: never requested."""

    @property
    def requested(self) -> bool:
        return False

    def cause(self) -> StopCause | None:
        return None

    def wait(self, timeout: timedelta) -> bool:
        return False


NEVER_CANCEL: Final[CancelSignal] = _NeverCancel()


@dataclass(frozen=True, slots=True)
class Call:
    """What one docker invocation came to.

    `started` is false when the port could not start the executable (nothing ran). `interrupted`
    is the port's code (EXECUTION_CANCELLED / EXECUTION_DEADLINE) when it ended the process.
    `exit_status` is the process's, `output` the console tail (evidence, never protocol)."""

    argv: tuple[str, ...]
    started: bool
    exit_status: int | None
    output: str
    interrupted: str | None = None

    @property
    def answered(self) -> bool:
        """Exit 0: the engine answered (V-10.4 `observe_ok_exit`)."""
        return self.started and self.interrupted is None and self.exit_status == 0


class DockerCli:
    """The docker executable, the bound endpoint and the injected `ExecutionPort` (MC-B-01)."""

    def __init__(
        self,
        cli: str | os.PathLike[str],
        endpoint: str | None,
        execution: ExecutionPort,
        cancel: CancelSignal | None = None,
        call_bound: timedelta = CALL_BOUND,
    ) -> None:
        text = os.fspath(cli)
        if not os.path.isabs(text):
            raise ValueError("the docker executable path must be absolute (B3-C14)")
        self.executable = text
        self.endpoint = endpoint
        self.execution = execution
        self.cancel = NEVER_CANCEL if cancel is None else cancel
        self.call_bound = call_bound

    def argv(self, *args: str) -> tuple[str, ...]:
        """`<docker> [--host <endpoint>] <args>`: the shape every descriptor argv has (MC-B-01)."""
        host = ("--host", self.endpoint) if self.endpoint else ()
        return (self.executable, *host, *args)

    def call(self, args: Sequence[str], ticket: AttemptTicket | None = None) -> Call:
        """Run `docker <args>` once through the execution port. A read passes no ticket; an effect
        passes its own, unchanged."""
        argv = self.argv(*args)
        command = BoundCommand(
            task=f"docker.{args[0]}" if args else "docker",
            argv=argv,
            environment={},
            resolved=Resolved(self.executable, "", "", ""),
            reports_tests=False,
        )
        until = datetime.now(UTC) + self.call_bound
        confirmation, result = self.execution.run(
            command, _READ_TICKET if ticket is None else ticket, self.cancel, until
        )
        if ConfirmationStatus(getattr(confirmation.status, "value", confirmation.status)) is (
            ConfirmationStatus.NOT_APPLIED
        ):
            return Call(argv, started=False, exit_status=None, output="")
        if result is None:  # V-5.5: only a NOT_APPLIED confirmation carries no result
            return Call(argv, started=True, exit_status=None, output="")
        klass = ExecutionClass(getattr(result.classification, "value", result.classification))
        interrupted = result.code if klass is ExecutionClass.INTERRUPTED else None
        return Call(argv, True, result.exit_status, result.excerpt, interrupted)


def engine_state(
    cli: str | os.PathLike[str],
    endpoint: str | None,
    execution: ExecutionPort,
    cancel: CancelSignal | None = None,
) -> Reachable | Unreachable | CliMissing:
    """Whether the engine at `endpoint` answers, and its server version (B3-C1, V-3.8).

    `Reachable(server_version)` only when the CLI ran, exited 0 and printed a version;
    `CliMissing` when the executable could not be started; otherwise `Unreachable`. Never raises
    for either condition, and the two are different codes (`WR-VERIFY-3`)."""
    docker = DockerCli(cli, endpoint, execution, cancel)
    call = docker.call(("info", "--format", "{{.ServerVersion}}"))
    if not call.started:
        return CliMissing(docker.executable, "the executable could not be started")
    if call.interrupted is not None:
        return Unreachable(f"no answer from the engine ({call.interrupted})")
    version = call.output.strip().splitlines()[-1].strip() if call.output.strip() else ""
    if call.exit_status == 0 and version:
        return Reachable(version)
    return Unreachable(call.output.strip()[:200] or f"exit status {call.exit_status}")
