"""Real local-process resource port (L.SL-3.3; B3-C1..C5, B3-C21 Local Process Supervision).

`LocalProcessPort` implements `ResourceReads`, `ResourceCreate` (for `AGENT_LAUNCHED_PROJECT`) and
`ResourceOwned` over processes it starts itself:

- **create** launches `spec.command` (its `argv[0]` is `resolved.executable`, else the call raises
  before any change, B3-E1) as a child of the caller in the run's process group and session, with
  stdin closed and a scrubbed `PATH`; it never detaches what it starts (B3-C4, V-2.3). It is
  idempotent per run-scoped selector: a live instance for `(lineage, effect)` is returned as it is.
  A `DURABLE` creation is refused before any change: a process this port starts is the run's, never
  a host-kept one (its descriptor is still derivable, `Durable(HOST)`, B3-C3).
- **observe** reports `selector_present` for this port's instance of this root's run-scoped
  selector only, proven by the process's numeric start time (`identity.py`, DM-66), never by port
  occupancy (WR-OWN-7); any other process that runs the same command line is a `FoundRef` and is
  never signalled: `stop`, `restart` and `recreate` act only on an instance this port recorded
  (pid and start), and refuse (`NOT_APPLIED`, no signal) for a selector it does not hold.
- **restart / recreate** are one ticketed repair on the same handle: stop, observe until absent,
  launch again from the recorded spec under the same selector and bound command (B3-C21). The old
  process is ended and reaped before the new one starts, in the run's group like every launch; a
  relaunch that cannot start after the old one was stopped is `UNKNOWN`, never `NOT_APPLIED`
  (L.RB-8.1, B3-C16); a selector this port does not hold is `NOT_APPLIED` with no signal sent.
- **check** `ready` holds when the recorded instance is alive; **endpoint** for `HOST` is
  `127.0.0.1:<PORT>` where `PORT` is the bound command's declared environment value, else
  `RouteRefused`; from a container it is `RouteRefused(ROUTE_UNSUPPORTED)` (B3-C2, D-4).
- **the endpoint contract for `PORT=0`** (RACES-REPORT L-17): a bound command that declares
  `PORT=0` chooses its own port. The port adds `TRESTLE_ENDPOINT_FILE` to its environment, naming a
  file under the port's endpoint directory; the process writes its bound address there,
  `127.0.0.1:<port>`, atomically (a temporary beside it, then `os.replace`), once it accepts
  connections. `create` (and a restart's relaunch) returns only once that file is there, within
  `LISTEN_WAIT_S`; a process that exits first or never writes it is ended and the creation is
  `UNKNOWN` (it started, so something changed). `endpoint` then reads the address from the file,
  so no caller ever guesses a free port the process might not get.

Every instance is a child of this port object: a fresh port object holds none, and sees an earlier
attempt's process as found. Executor-chosen values (the contract names none): the selector spelling
(`proc-<16 hex>`), the found selector (`found-<pid>-<start>`), the `PORT` convention, matching a
found process by the arguments after `argv[0]` (a launcher may rewrite `argv[0]`), FOUND_MAX 16,
the SIGTERM-to-SIGKILL grace, and `launch_policy` = `DISABLED_BY_CONFIGURATION` / `CONTAINED`.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from trestle.workflow.declarations import EffectId, Lifetime, RealizationKind, Vantage
from trestle.workflow.ports import (
    Durable,
    DurableOwner,
    EffectCall,
    Endpoint,
    ExecutionPolicy,
    Helpers,
    InRunGroup,
    ReleaseDescriptor,
    ResourceObservation,
    ResourceSpec,
    RouteRefused,
    SelfProvisioning,
)
from trestle.workflow.services import AttemptTicket
from trestle.workflow.values import (
    CheckResult,
    Confirmation,
    ConfirmationStatus,
    CreatedHandle,
    FoundRef,
    Lineage,
    OwnedHandle,
    SelectorRef,
)

from trestle_packs.process import identity
from trestle_packs.process.command import TOOLCHAIN_MISSING, scrubbed_path

ROUTE_UNSUPPORTED = "admission.route_unsupported"
RESOURCE_KIND = "local_process"
FOUND_MAX = 16  # V-13
PORT_ENV = "PORT"
_GRACE_S = 2.0  # SIGTERM to SIGKILL
ENDPOINT_FILE_ENV: Final = "TRESTLE_ENDPOINT_FILE"
ENDPOINT_DIR_PREFIX: Final = "trestle-local-endpoints-"  # under the process's TMPDIR
LISTEN_WAIT_S: Final = 10.0  # how long a `PORT=0` launch may take to report its endpoint
_LISTEN_POLL_S = 0.05

type Target = CreatedHandle | OwnedHandle | FoundRef | SelectorRef


def run_scoped_selector(lineage: Lineage, effect: EffectId) -> str:
    """Depends only on `(lineage, effect)` (V-10.1)."""
    path = "/".join(lineage.path.segments)
    digest = hashlib.sha256(f"{lineage.root_run_id}\0{path}\0{effect}".encode()).hexdigest()
    return f"proc-{digest[:16]}"


def found_selector(pid: int, start: int) -> str:
    return f"found-{pid}-{start}"


def _found_identity(selector: str) -> tuple[int, int] | None:
    parts = selector.split("-")
    if len(parts) == 3 and parts[0] == "found" and parts[1].isdigit() and parts[2].isdigit():
        return int(parts[1]), int(parts[2])
    return None


def same_command_line(args: str, argv: tuple[str, ...]) -> bool:
    """`ps` shows the arguments after `argv[0]` as launched; a launcher may rewrite `argv[0]`."""
    tail = " ".join(argv[1:])
    if not tail:
        return (
            args == argv[0]
            or args.split(" ", 1)[0].rsplit("/", 1)[-1] == argv[0].rsplit("/", 1)[-1]
        )
    return args.endswith(" " + tail)


def declared_port(spec: ResourceSpec) -> int | None:
    raw = None if spec.command is None else spec.command.environment.get(PORT_ENV)
    return int(raw) if raw is not None and raw.isdigit() else None


def _reported(path: Path | None) -> int | None:
    """The port a `PORT=0` process reported in its endpoint file, or None while it has not."""
    if path is None:
        return None
    try:
        host, _, port = path.read_text(encoding="utf-8").strip().rpartition(":")
    except OSError:
        return None
    return int(port) if host and port.isdigit() else None


@dataclass
class _Instance:
    selector: str
    spec: ResourceSpec
    proc: subprocess.Popen[bytes]
    start: int | None
    endpoint_file: Path | None = None


class LocalProcessPort:
    """`ResourceReads` + `ResourceCreate` + `ResourceOwned` over local child processes."""

    def __init__(self, endpoint_dir: Path | None = None) -> None:
        self._instances: dict[str, _Instance] = {}
        self._endpoint_dir = endpoint_dir
        self._owns_endpoint_dir = False  # made on the first `PORT=0` launch, removed by `close`

    # ------------------------------------------------------------------ lifecycle of the port

    def close(self) -> None:
        """End every process this port started (each by its own handle)."""
        for instance in list(self._instances.values()):
            self._end(instance)
        self._instances.clear()
        if self._owns_endpoint_dir and self._endpoint_dir is not None:
            shutil.rmtree(self._endpoint_dir, ignore_errors=True)
            self._endpoint_dir, self._owns_endpoint_dir = None, False

    def inventory(self) -> dict[str, frozenset[str]]:
        """What exists, by category, for the suite's engine-inventory reach (B3-C17)."""
        live = frozenset(s for s, i in self._instances.items() if self._alive(i))
        empty: frozenset[str] = frozenset()
        return {"containers": live, "images": empty, "volumes": empty, "networks": empty}

    # ------------------------------------------------------------------ ResourceReads

    def observe(
        self, spec: ResourceSpec, lineage: Lineage, effect: EffectId | None
    ) -> ResourceObservation:
        selector = None if effect is None else run_scoped_selector(lineage, effect)
        own = None if selector is None else self._instances.get(selector)
        present = own is not None and self._alive(own)
        rows = identity.listing()
        if rows is None:  # could not observe (V-3.8): never a partial observation
            return ResourceObservation(False, None, False, False, (), (), TOOLCHAIN_MISSING)
        now = datetime.now(UTC)
        found: list[FoundRef] = []
        if spec.command is not None:
            skip = own.proc.pid if present and own is not None else None
            for pid, args in rows:
                if pid == skip or not same_command_line(args, tuple(spec.command.argv)):
                    continue
                start = identity.start_time(pid)
                if start is not None:
                    found.append(FoundRef(RESOURCE_KIND, found_selector(pid, start), now))
        found = sorted(found, key=lambda f: f.selector)[:FOUND_MAX]
        ref = (
            SelectorRef(lineage, effect, selector, now)
            if present and selector is not None and effect is not None
            else None
        )
        return ResourceObservation(
            selector_present=present,
            selector_ref=ref,
            identity_proven=present or bool(found),
            configuration_compatible=True,
            currency=(),
            found=tuple(found),
            code=None,
        )

    def check(self, check: str, target: Target) -> CheckResult:
        if check != "ready":
            return CheckResult(False, None, f"unsupported check {check!r}")
        if self._target_alive(target.selector):
            return CheckResult(True, None, "ready holds")
        return CheckResult(False, None, f"{target.selector} is not running")

    def endpoint(self, target: Target, vantage: Vantage) -> Endpoint | RouteRefused:
        if getattr(vantage, "value", vantage) != "host":
            return RouteRefused(ROUTE_UNSUPPORTED, "A local process has no route from a container.")
        instance = self._instances.get(target.selector)
        port = None if instance is None else declared_port(instance.spec)
        if port is None:
            return RouteRefused(ROUTE_UNSUPPORTED, "The bound command declares no PORT to reach.")
        if port == 0:
            assert instance is not None
            port = _reported(instance.endpoint_file)
            if port is None:  # only reachable when a caller bypassed `create`
                return RouteRefused(ROUTE_UNSUPPORTED, "The process has not reported its endpoint.")
        return Endpoint("http", "127.0.0.1", port)

    # ------------------------------------------------------------------ ResourceCreate

    def launch_policy(self, spec: ResourceSpec) -> ExecutionPolicy:
        """Pure: every process a launch starts stays attributable to the run (V-2.3)."""
        return ExecutionPolicy(SelfProvisioning.DISABLED_BY_CONFIGURATION, Helpers.CONTAINED, None)

    def release_descriptor(self, call: EffectCall) -> ReleaseDescriptor:
        if call.member == "create":
            if getattr(call.lifetime, "value", call.lifetime) == Lifetime.DURABLE.value:
                return Durable(DurableOwner.HOST)
            spec = call.arguments["spec"]
            helpers = self.launch_policy(spec).helpers  # type: ignore[arg-type]
            return InRunGroup(helpers is Helpers.DISCLOSED)
        target: Any = call.arguments["target"]  # restart, recreate, stop: the handle's own
        release: ReleaseDescriptor = target.release
        return release

    def create(self, spec: ResourceSpec, ticket: AttemptTicket) -> Confirmation:
        self._require_launchable(spec)
        if getattr(ticket.lifetime, "value", ticket.lifetime) == Lifetime.DURABLE.value:
            raise ValueError("a local process is the run's: it has no DURABLE form (V-10.2)")
        selector = run_scoped_selector(ticket.lineage, ticket.effect)
        existing = self._instances.get(selector)
        if existing is not None and self._alive(existing):  # idempotent per selector (B3-C4)
            return Confirmation(ConfirmationStatus.APPLIED, None, selector)
        return self._launch(selector, spec)

    # ------------------------------------------------------------------ ResourceOwned

    def restart(self, target: OwnedHandle, ticket: AttemptTicket) -> Confirmation:
        return self._relaunch(target)

    def recreate(self, target: OwnedHandle, ticket: AttemptTicket) -> Confirmation:
        return self._relaunch(target)

    def stop(self, target: CreatedHandle, ticket: AttemptTicket) -> Confirmation:
        instance = self._instances.pop(target.selector, None)
        if instance is None:  # not this port's: nothing is signalled, nothing changed
            return Confirmation(ConfirmationStatus.NOT_APPLIED, None, None)
        self._end(instance)
        return Confirmation(ConfirmationStatus.APPLIED, None, target.selector)

    # ------------------------------------------------------------------ internals

    def _require_launchable(self, spec: ResourceSpec) -> None:
        command = spec.command
        kind = getattr(spec.realization, "value", spec.realization)
        if kind != RealizationKind.AGENT_LAUNCHED_PROJECT.value or command is None:
            raise ValueError("a launch needs an AGENT_LAUNCHED_PROJECT spec with a command (B3-C4)")
        if tuple(command.argv)[:1] != (command.resolved.executable,):
            raise ValueError("spec.command.argv[0] must equal command.resolved.executable (B3-C4)")

    def _launch(self, selector: str, spec: ResourceSpec) -> Confirmation:
        command = spec.command
        assert command is not None
        env: dict[str, str] = {
            **command.environment,
            "PATH": scrubbed_path(command.resolved.executable),
        }
        endpoint_file = None
        if declared_port(spec) == 0:
            endpoint_file = self._endpoints() / f"{selector}.endpoint"
            endpoint_file.unlink(missing_ok=True)
            env[ENDPOINT_FILE_ENV] = str(endpoint_file)
        try:
            proc = subprocess.Popen(
                tuple(command.argv),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
            )  # the caller's process group and session: never detached
        except (FileNotFoundError, PermissionError, NotADirectoryError):
            return Confirmation(ConfirmationStatus.NOT_APPLIED, TOOLCHAIN_MISSING, None)
        instance = _Instance(selector, spec, proc, identity.start_time(proc.pid), endpoint_file)
        if endpoint_file is not None and not self._await_endpoint(instance):
            self._end(instance)  # started and ended: something changed, so never NOT_APPLIED
            return Confirmation(ConfirmationStatus.UNKNOWN, None, None)
        self._instances[selector] = instance
        return Confirmation(ConfirmationStatus.APPLIED, None, selector)

    def _endpoints(self) -> Path:
        if self._endpoint_dir is None:
            self._endpoint_dir = Path(tempfile.mkdtemp(prefix=ENDPOINT_DIR_PREFIX))
            self._owns_endpoint_dir = True
        return self._endpoint_dir

    def _await_endpoint(self, instance: _Instance) -> bool:
        """Until the process reports its endpoint (True), exits, or `LISTEN_WAIT_S` passes."""
        end = time.monotonic() + LISTEN_WAIT_S
        while _reported(instance.endpoint_file) is None:
            if instance.proc.poll() is not None or time.monotonic() >= end:
                return False
            time.sleep(_LISTEN_POLL_S)
        return True

    def _relaunch(self, target: OwnedHandle) -> Confirmation:
        instance = self._instances.pop(target.selector, None)
        if instance is None:
            return Confirmation(ConfirmationStatus.NOT_APPLIED, None, None)
        self._end(instance)  # observed absent: the child is reaped before it is launched again
        made = self._launch(instance.selector, instance.spec)
        if made.status is ConfirmationStatus.NOT_APPLIED:
            # the old process is gone and the new one never started: something changed, so
            # NOT_APPLIED would be a claim the adapter cannot prove (B3-C16)
            return Confirmation(ConfirmationStatus.UNKNOWN, made.code, instance.selector)
        return made

    def _alive(self, instance: _Instance) -> bool:
        """Running, and the very process recorded (a reused pid has another start time)."""
        if instance.proc.poll() is not None:
            return False
        return identity.start_time(instance.proc.pid) == instance.start

    def _target_alive(self, selector: str) -> bool:
        instance = self._instances.get(selector)
        if instance is not None:
            return self._alive(instance)
        found = _found_identity(selector)
        return found is not None and identity.start_time(found[0]) == found[1]

    def _end(self, instance: _Instance) -> None:
        proc = instance.proc
        if proc.poll() is None and identity.start_time(proc.pid) == instance.start:
            proc.terminate()
            try:
                proc.wait(timeout=_GRACE_S)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        else:
            proc.wait()
