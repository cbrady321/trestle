"""Fake local-process resource port (L.SL-3.3; B3-C1..C5, MC-25).

Stdlib only, like its siblings. `FakeLocalProcess` implements `ResourceReads`, `ResourceCreate`
and `ResourceOwned` for local processes with the same rules as the real port
(`trestle_packs.process.local.LocalProcessPort`) and starts nothing: a simulated process table
holds the instances. It is the second implementation of the Local Process Supervision family
(`[fake-local]`); `differ d8 --pair process` drives it and the real port through one scenario and
compares every answer.

The rules, the same as the real port's: a launch needs an `AGENT_LAUNCHED_PROJECT` spec with a
bound command; creation is idempotent per run-scoped selector (`proc-<16 hex>`) and refuses a
`DURABLE` creation before any change (its descriptor is still `Durable(host)`); `observe` reports
`selector_present` for this fake's instance of the selector only, and any other live process that
runs the same command line (`plant_found`, or another selector's instance) is a `FoundRef`;
`stop`, `restart` and `recreate` act only on an instance the fake holds (else `NOT_APPLIED`);
`check("ready")` holds while the instance is alive; the `HOST` endpoint is `127.0.0.1:<PORT>` from
the command's declared environment. A restart ends the old process before the new one starts (its
`events()` read `start`, `stop`, `start`); one whose relaunch cannot start after the old process was
stopped is `UNKNOWN`, never `NOT_APPLIED` (L.RB-8.1, B3-C16).
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import count
from typing import Any

from trestle_packs.fakes.command import (
    ROUTE_UNSUPPORTED,
    Confirmation,
    ConfirmationStatus,
    ExecutionPolicy,
    Helpers,
    SelfProvisioning,
    _value,
    durable,
    in_run_group,
)
from trestle_packs.fakes.marker import (
    FOUND_MAX,
    CheckResult,
    Endpoint,
    FoundRef,
    ResourceObservation,
    RouteRefused,
    SelectorRef,
)

RESOURCE_KIND = "local_process"
PORT_ENV = "PORT"
TOOLCHAIN_MISSING = "execution.toolchain_missing"


def run_scoped_selector(lineage: Any, effect: str) -> str:
    """Depends only on `(lineage, effect)` (V-10.1); the real port spells it the same way."""
    path = "/".join(lineage.path.segments)
    digest = hashlib.sha256(f"{lineage.root_run_id}\0{path}\0{effect}".encode()).hexdigest()
    return f"proc-{digest[:16]}"


def found_selector(pid: int, start: int) -> str:
    return f"found-{pid}-{start}"


@dataclass
class _Process:
    pid: int
    start: int
    argv: tuple[str, ...]
    alive: bool = True


class FakeLocalProcess:
    """`ResourceReads` + `ResourceCreate` + `ResourceOwned` over a simulated process table."""

    def __init__(self, *, first_pid: int = 41000) -> None:
        self._pids = count(first_pid)
        self._clock = count(1_000_000)  # a numeric start token, one per process
        self._table: list[_Process] = []
        self._instances: dict[str, tuple[_Process, Any]] = {}  # selector -> (process, spec)
        self._events: list[str] = []  # `start` / `stop` of the instances this fake launched
        self._launch_fails = False

    # ------------------------------------------------------------------ the fake's own controls

    def close(self) -> None:
        for process in self._table:
            process.alive = False
        self._instances.clear()

    def events(self) -> list[str]:
        """The order in which this fake started (`start`) and ended (`stop`) its instances; an
        instance killed out of band (`kill`) logs no `stop`, as a killed process would not."""
        return list(self._events)

    def break_launch(self) -> None:
        """From now on a launch cannot start (the bound command is gone): a create returns
        `NOT_APPLIED`, a restart that already stopped the old process returns `UNKNOWN`."""
        self._launch_fails = True

    def kill(self, selector: str) -> None:
        """End an instance out of band (a crash): no `stop` is logged and it stays in the table
        as a dead process the port still holds."""
        held = self._instances.get(selector)
        if held is not None:
            held[0].alive = False

    def token_of(self, selector: str) -> str | None:
        """A token naming the process behind `selector` (its pid and start token), or None."""
        held = self._instances.get(selector)
        if held is not None:
            return f"{held[0].pid}-{held[0].start}"
        parts = selector.split("-")
        if len(parts) == 3 and parts[0] == "found":
            return f"{parts[1]}-{parts[2]}"
        return None

    def alive(self, token: str) -> bool:
        pid, _, start = token.partition("-")
        return any(p.alive and (str(p.pid), str(p.start)) == (pid, start) for p in self._table)

    def in_run_group(self, token: str) -> bool:
        """Every process this fake launches is in the run's group by construction; a planted
        found process is not one it launched."""
        pid, _, start = token.partition("-")
        launched = {(str(p.pid), str(p.start)) for p, _ in self._instances.values()}
        return (pid, start) in launched

    def plant_found(self, logical_system: str, argv: Sequence[str] = ()) -> str:
        """A live process this fake's selectors do not name, running `argv` (a found instance)."""
        process = self._spawn(tuple(argv))
        return found_selector(process.pid, process.start)

    def inventory(self) -> dict[str, frozenset[str]]:
        live = frozenset(s for s, (p, _) in self._instances.items() if p.alive)
        empty: frozenset[str] = frozenset()
        return {"containers": live, "images": empty, "volumes": empty, "networks": empty}

    # ------------------------------------------------------------------ ResourceReads

    def observe(self, spec: Any, lineage: Any, effect: str | None) -> ResourceObservation:
        selector = None if effect is None else run_scoped_selector(lineage, effect)
        held = None if selector is None else self._instances.get(selector)
        own = held[0] if held is not None and held[0].alive else None
        now = datetime.now(UTC)
        found: list[FoundRef] = []
        if spec.command is not None:
            argv = tuple(spec.command.argv)
            for process in self._table:
                if process.alive and process is not own and process.argv[1:] == argv[1:]:
                    found.append(
                        FoundRef(RESOURCE_KIND, found_selector(process.pid, process.start), now)
                    )
        found = sorted(found, key=lambda f: f.selector)[:FOUND_MAX]
        ref = (
            SelectorRef(lineage, effect, selector, now)
            if own is not None and selector is not None and effect is not None
            else None
        )
        return ResourceObservation(
            selector_present=own is not None,
            selector_ref=ref,
            identity_proven=own is not None or bool(found),
            configuration_compatible=True,
            currency=(),
            found=tuple(found),
            code=None,
        )

    def check(self, check: str, target: Any) -> CheckResult:
        if check != "ready":
            return CheckResult(False, None, f"unsupported check {check!r}")
        if self._alive(target.selector):
            return CheckResult(True, None, "ready holds")
        return CheckResult(False, None, f"{target.selector} is not running")

    def endpoint(self, target: Any, vantage: Any) -> Endpoint | RouteRefused:
        if _value(vantage) != "host":
            return RouteRefused(ROUTE_UNSUPPORTED, "A local process has no route from a container.")
        held = self._instances.get(target.selector)
        raw = (
            None
            if held is None or held[1].command is None
            else held[1].command.environment.get(PORT_ENV)
        )
        if raw is None or not raw.isdigit():
            return RouteRefused(ROUTE_UNSUPPORTED, "The bound command declares no PORT to reach.")
        return Endpoint("http", "127.0.0.1", int(raw))

    # ------------------------------------------------------------------ ResourceCreate

    def launch_policy(self, spec: Any) -> ExecutionPolicy:
        return ExecutionPolicy(SelfProvisioning.DISABLED_BY_CONFIGURATION, Helpers.CONTAINED, None)

    def release_descriptor(self, call: Any) -> dict[str, Any]:
        if call.member == "create":
            if _value(call.lifetime) == "durable":
                return durable("host")
            helpers = self.launch_policy(call.arguments["spec"]).helpers
            return in_run_group(helpers is Helpers.DISCLOSED)
        release: dict[str, Any] = call.arguments["target"].release
        return release

    def create(self, spec: Any, ticket: Any) -> Confirmation:
        self._require_launchable(spec)
        if _value(ticket.lifetime) == "durable":
            raise ValueError("a local process is the run's: it has no DURABLE form (V-10.2)")
        selector = run_scoped_selector(ticket.lineage, ticket.effect)
        held = self._instances.get(selector)
        if held is not None and held[0].alive:  # idempotent per selector (B3-C4)
            return Confirmation(ConfirmationStatus.APPLIED, None, selector)
        if self._launch_fails:
            return Confirmation(ConfirmationStatus.NOT_APPLIED, TOOLCHAIN_MISSING, None)
        self._instances[selector] = (self._launched(tuple(spec.command.argv)), spec)
        return Confirmation(ConfirmationStatus.APPLIED, None, selector)

    # ------------------------------------------------------------------ ResourceOwned

    def restart(self, target: Any, ticket: Any) -> Confirmation:
        return self._relaunch(target.selector)

    def recreate(self, target: Any, ticket: Any) -> Confirmation:
        return self._relaunch(target.selector)

    def stop(self, target: Any, ticket: Any) -> Confirmation:
        held = self._instances.pop(target.selector, None)
        if held is None:  # not this fake's: nothing changed
            return Confirmation(ConfirmationStatus.NOT_APPLIED, None, None)
        self._ended(held[0])
        return Confirmation(ConfirmationStatus.APPLIED, None, target.selector)

    # ------------------------------------------------------------------ internals

    def _require_launchable(self, spec: Any) -> None:
        command = spec.command
        if _value(spec.realization) != "agent_launched_project" or command is None:
            raise ValueError("a launch needs an AGENT_LAUNCHED_PROJECT spec with a command (B3-C4)")
        if tuple(command.argv)[:1] != (command.resolved.executable,):
            raise ValueError("spec.command.argv[0] must equal command.resolved.executable (B3-C4)")

    def _spawn(self, argv: tuple[str, ...]) -> _Process:
        process = _Process(next(self._pids), next(self._clock), argv)
        self._table.append(process)
        return process

    def _alive(self, selector: str) -> bool:
        held = self._instances.get(selector)
        if held is not None:
            return held[0].alive
        parts = selector.split("-")
        if len(parts) == 3 and parts[0] == "found" and parts[1].isdigit() and parts[2].isdigit():
            return any(
                p.alive and p.pid == int(parts[1]) and p.start == int(parts[2]) for p in self._table
            )
        return False

    def _launched(self, argv: tuple[str, ...]) -> _Process:
        self._events.append("start")
        return self._spawn(argv)

    def _ended(self, process: _Process) -> None:
        if process.alive:
            self._events.append("stop")
        process.alive = False

    def _relaunch(self, selector: str) -> Confirmation:
        held = self._instances.pop(selector, None)
        if held is None:
            return Confirmation(ConfirmationStatus.NOT_APPLIED, None, None)
        self._ended(held[0])  # observed absent before the new process starts
        if self._launch_fails:  # the old process is gone and the new one cannot start (B3-C16)
            return Confirmation(ConfirmationStatus.UNKNOWN, TOOLCHAIN_MISSING, selector)
        self._instances[selector] = (self._launched(held[0].argv), held[1])
        return Confirmation(ConfirmationStatus.APPLIED, None, selector)
