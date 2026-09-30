"""Fake Docker engine and container port (L.NW-2.4; B3-C1..C6, B3-C17, MC-B-01, MC-25, DM-09).

Stdlib only, like its siblings (a fake needs no `trestle` install): the values it returns carry the
field names of the B3 types they stand for, and a release descriptor is its wire mapping.
`FakeContainerEngine` implements `ResourceReads`, `ResourceCreate`, `ResourceOwned` and
`ResourceSafeStart` over a simulated Docker engine and answers the descriptor's own argvs through
`run_argv`, so the conformance suite can run a `ArgvRelease` (observe -> stop -> remove -> observe)
against the fake as the host sweep runs it against the real engine.

The engine holds containers, images, volumes and networks. It follows Docker where the contract
leans on it: `ps -aq --filter name=^/x$` lists a stopped container too and exits 0 whenever the
engine answered; `rm` refuses a running container; a stopped, never a removed, container is still
listed. It is STRICTER than Docker in one place: any volume flag on `rm` deletes the engine's
volumes, so a defective descriptor (`-v`) is caught by behaviour and not only by its argv (X-11).

Knobs: `reachable=False` (the engine does not answer: exit 1 / `DOCKER_ENGINE_UNREACHABLE`) and
`cli_present=False` (the docker executable is not there: `DOCKER_CLI_MISSING`).
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
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
)
from trestle_packs.fakes.marker import (
    CheckResult,
    Endpoint,
    FoundRef,
    ResourceObservation,
    RouteRefused,
    SelectorRef,
)

DOCKER_CLI_MISSING = "adapter.docker_cli_missing"
DOCKER_ENGINE_UNREACHABLE = "adapter.docker_engine_unreachable"
DOCKER_KIND = "docker_container"  # the FoundRef resource kind of a container
ENGINE_KIND = "docker_engine"  # a found shared engine (start's HOST case)
SELECTOR_PREFIX = "trwr-"
FOUND_MAX = 16
_SELECTOR_CHARS = re.compile(r"[^a-z0-9_.-]")


def selector_name(lineage: Any) -> str:
    """MC-B-01: `trwr-<root run_id>-<path>`, `path` the node path segments joined by `.`, lowercase
    `[a-z0-9_.-]`. Depends only on the lineage."""
    path = _SELECTOR_CHARS.sub("_", ".".join(lineage.path.segments).lower())
    return f"{SELECTOR_PREFIX}{lineage.root_run_id}-{path}"


def _filter_name(selector: str) -> str:
    """The exact-name filter value: `^/<selector>$`, `.` escaped so a dot in a path is a dot."""
    return "^/" + selector.replace(".", r"\.") + "$"


def argv_release(
    executable: str,
    endpoint: str | None,
    selector: str,
    timeout_s: float,
) -> dict[str, Any]:
    """The V-10 `ArgvRelease` wire mapping MC-B-01 pins for a `RUN` container create."""
    host = ["--host", endpoint] if endpoint else []
    return {
        "form": "argv",
        "executable": executable,
        "observe_argv": [
            executable,
            *host,
            "ps",
            "-aq",
            "--no-trunc",
            "--filter",
            f"name={_filter_name(selector)}",
        ],
        "observe_ok_exit": [0],
        "stop_argv": [executable, *host, "stop", selector],
        "timeout_s": timeout_s,
        "remove_argv": [executable, *host, "rm", selector],
    }


@dataclass
class _Container:
    name: str
    state: str  # running | exited
    container_port: int
    host_port: int


def _id(name: str) -> str:
    return hashlib.sha256(name.encode()).hexdigest()


def _host_port(name: str) -> int:
    return 32768 + int(hashlib.sha256(name.encode()).hexdigest()[:4], 16) % 28000


class FakeContainerEngine:
    """`ResourceReads` + `ResourceCreate` + `ResourceOwned` + `ResourceSafeStart` over a simulated
    Docker engine, and the engine's CLI for a descriptor's argv (`run_argv`)."""

    def __init__(
        self,
        *,
        executable: str = "/fake/bin/docker",
        endpoint: str | None = "unix:///fake/engine.sock",
        reachable: bool = True,
        cli_present: bool = True,
        container_port: int = 8080,
        default_release_timeout_s: float = 30.0,
    ) -> None:
        self.executable = executable
        self.docker_endpoint = endpoint  # the bound `--host` (I-4); `endpoint` is the read member
        self.reachable = reachable
        self.cli_present = cli_present
        self.container_port = container_port
        self.default_release_timeout_s = default_release_timeout_s
        self._containers: dict[str, _Container] = {}
        self._volumes: set[str] = set()
        self._images: set[str] = {"alpine"}
        self._networks: set[str] = {"bridge"}
        self._created: set[str] = set()  # selectors this adapter made (recreate's proof)

    # ------------------------------------------------------------------ the fake's own surface

    def close(self) -> None:
        self._containers.clear()

    def inventory(self) -> dict[str, frozenset[str]]:
        """What exists, by category (the suite's engine-inventory reach, B3-C17 (3))."""
        return {
            "containers": frozenset(self._containers),
            "images": frozenset(self._images),
            "volumes": frozenset(self._volumes),
            "networks": frozenset(self._networks),
        }

    def plant_found(self, system: str, running: bool = True) -> str:
        """A container this root's selector does not name: it runs under `system`'s own name."""
        self._containers[system] = _Container(
            system, "running" if running else "exited", self.container_port, _host_port(system)
        )
        return system

    def seed_volume(self, name: str) -> None:
        self._volumes.add(name)

    def _down(self) -> str | None:
        """The could-not-observe code when the CLI is missing or the engine does not answer."""
        if not self.cli_present:
            return DOCKER_CLI_MISSING
        return None if self.reachable else DOCKER_ENGINE_UNREACHABLE

    # ------------------------------------------------------------------ the engine's CLI

    def run_argv(self, argv: Sequence[str]) -> tuple[int, str]:
        """Run one docker argv as the host sweep runs a descriptor's: `(exit status, stdout)`."""
        args = list(argv)
        if not self.cli_present or not args or args[0] != self.executable:
            return 127, ""
        args = args[1:]
        if args[:1] == ["--host"]:
            args = args[2:]
        if not self.reachable:
            return 1, ""  # "Cannot connect to the Docker daemon" is stderr, stdout is empty
        if not args:
            return 0, ""
        verb, rest = args[0], args[1:]
        if verb == "ps":
            return 0, self._ps(rest)
        names = [a for a in rest if not a.startswith("-")]
        if verb == "stop":
            hit = [self._containers[n] for n in names if n in self._containers]
            for c in hit:
                c.state = "exited"
            return (
                (0, "\n".join(c.name for c in hit)) if hit and len(hit) == len(names) else (1, "")
            )
        if verb == "rm":
            if any(n not in self._containers for n in names):
                return 1, ""
            if any(self._containers[n].state == "running" for n in names) and not (
                {"-f", "--force"} & set(rest)
            ):
                return 1, ""  # cannot remove a running container
            for n in names:
                del self._containers[n]
            if {"-v", "--volumes"} & set(rest):
                self._volumes.clear()  # the fake is stricter than docker (module docstring)
            return 0, "\n".join(names)
        return 2, ""

    def _ps(self, rest: list[str]) -> str:
        every = any(a in ("-a", "-aq", "--all") for a in rest)
        pattern = None
        for i, a in enumerate(rest):
            if a == "--filter" and i + 1 < len(rest) and rest[i + 1].startswith("name="):
                pattern = re.compile(rest[i + 1][len("name=") :])
        rows = []
        for c in self._containers.values():
            if not every and c.state != "running":
                continue
            if pattern is not None and not pattern.search("/" + c.name):
                continue
            rows.append(_id(c.name) if "--no-trunc" in rest else _id(c.name)[:12])
        return "\n".join(rows) + ("\n" if rows else "")

    # ------------------------------------------------------------------ ResourceReads

    def observe(self, spec: Any, lineage: Any, effect: str | None) -> ResourceObservation:
        down = self._down()
        if down is not None:
            return ResourceObservation(False, None, False, False, (), (), down)
        selector = None if effect is None else selector_name(lineage)
        present = selector is not None and selector in self._containers
        now = datetime.now(UTC)
        found = tuple(
            FoundRef(DOCKER_KIND, name, now)
            for name in sorted(self._containers)
            if name == spec.logical_system and name != selector
        )[:FOUND_MAX]
        ref = SelectorRef(lineage, effect, selector, now) if present and selector else None
        return ResourceObservation(present, ref, present, present, (), found, None)

    def check(self, check: str, target: Any) -> CheckResult:
        down = self._down()
        if down is not None:
            return CheckResult(False, down, "the engine could not be read")
        if check != "running":
            return CheckResult(False, None, f"{check} is not a check this engine knows")
        c = self._containers.get(target.selector)
        if c is not None and c.state == "running":
            return CheckResult(True, None, f"{target.selector} is running")
        return CheckResult(False, None, f"{target.selector} is not running")

    def endpoint(self, target: Any, vantage: Any) -> Endpoint | RouteRefused:
        down = self._down()
        if down is not None:
            return RouteRefused(down, "Start the Docker engine or install the CLI, then retry.")
        kind = getattr(target, "resource_kind", DOCKER_KIND)
        c = self._containers.get(target.selector)
        if kind not in (DOCKER_KIND, ENGINE_KIND) or c is None or c.state != "running":
            return RouteRefused(
                ROUTE_UNSUPPORTED,
                "Select a running Docker realization of this service reachable from its dependent.",
            )
        if _value(vantage) == "container":
            return Endpoint("tcp", c.name, c.container_port)
        return Endpoint("tcp", "127.0.0.1", c.host_port)

    # ------------------------------------------------------------------ ResourceCreate

    def launch_policy(self, spec: Any) -> ExecutionPolicy:
        return ExecutionPolicy(SelfProvisioning.DISABLED_BY_CONFIGURATION, Helpers.PREVENTED, None)

    def release_descriptor(self, call: Any) -> dict[str, Any]:
        member = call.member
        if member == "create":
            if _value(call.lifetime) == "durable":
                return durable("environment")
            timeout = (
                self.default_release_timeout_s
                if call.release_timeout is None
                else min(self.default_release_timeout_s, call.release_timeout.total_seconds())
            )
            selector = selector_name(call.lineage)
            return argv_release(self.executable, self.docker_endpoint, selector, timeout)
        if member == "start":
            target = call.arguments["target"]
            return durable(
                "host" if getattr(target, "resource_kind", "") == ENGINE_KIND else "environment"
            )
        return call.arguments["target"].release  # restart, recreate, stop: the handle's own

    def create(self, spec: Any, ticket: Any) -> Confirmation:
        if _value(spec.realization) != "docker_service":
            raise ValueError("a container port creates DOCKER_SERVICE resources only (B3-C4)")
        down = self._down()
        if down is not None:
            return Confirmation(ConfirmationStatus.NOT_APPLIED, down, None)
        selector = selector_name(ticket.lineage)
        if selector not in self._containers:  # idempotent per selector (B3-C4)
            self._containers[selector] = _Container(
                selector, "running", self.container_port, _host_port(selector)
            )
        self._created.add(selector)
        return Confirmation(ConfirmationStatus.APPLIED, None, selector)

    # ------------------------------------------------------------------ ResourceOwned

    def restart(self, target: Any, ticket: Any) -> Confirmation:
        down = self._down()
        if down is not None:
            return Confirmation(ConfirmationStatus.NOT_APPLIED, down, None)
        c = self._containers.get(target.selector)
        if c is None:
            return Confirmation(ConfirmationStatus.NOT_APPLIED, None, None)
        c.state = "running"
        return Confirmation(ConfirmationStatus.APPLIED, None, target.selector)

    def recreate(self, target: Any, ticket: Any) -> Confirmation:
        down = self._down()
        if down is not None:
            return Confirmation(ConfirmationStatus.NOT_APPLIED, down, None)
        if target.selector not in self._created:
            return Confirmation(ConfirmationStatus.NOT_APPLIED, None, None)
        self._containers[target.selector] = _Container(
            target.selector, "running", self.container_port, _host_port(target.selector)
        )
        return Confirmation(ConfirmationStatus.APPLIED, None, target.selector)

    def stop(self, target: Any, ticket: Any) -> Confirmation:
        down = self._down()
        if down is not None:
            return Confirmation(ConfirmationStatus.NOT_APPLIED, down, None)
        self._containers.pop(target.selector, None)  # stop, then remove, never a volume (B3-C5)
        return Confirmation(ConfirmationStatus.APPLIED, None, target.selector)

    # ------------------------------------------------------------------ ResourceSafeStart

    def start(self, target: Any, ticket: Any) -> Confirmation:
        down = self._down()
        if down is not None:
            return Confirmation(ConfirmationStatus.NOT_APPLIED, down, None)
        c = self._containers.get(target.selector)
        if c is None:
            return Confirmation(ConfirmationStatus.NOT_APPLIED, None, None)
        c.state = "running"  # stopped -> running only (B3-C6)
        return Confirmation(ConfirmationStatus.APPLIED, None, target.selector)
