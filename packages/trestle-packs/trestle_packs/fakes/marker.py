"""Fake marker resource (L.SV-5.16; B3-C1..C5, B3-C3, V-2.3, V-10, MC-25).

Stdlib only (see `command.py` for why values are defined here). `FakeMarker` implements
`ResourceReads`, `ResourceCreate` and `ResourceOwned` for a marker under `root`:

- A run-lifetime marker is a fake in-run process: a stdlib child process started in the caller's
  process group (attributable to the run, V-2.3) and observed by pid plus start token; its marker
  file is only its observable, so a killed group leaves nothing observable even if the file
  lingers. Its release descriptor is `InRunGroup(helpers_disclosed=False)`.
- A durable marker is a plain file. Its descriptor is `Durable(owner)` with the owner the fake is
  built with (default `environment`, B3-C3's provisioned-record case), and a file-backed marker
  never carries `InRunGroup` (MC-25). A `PROVISIONED` spec is durable whatever the lifetime asked.
- `FakeMarker(lifetime=...)` says what the fake models: a `run` fake serves both lifetimes (a
  RUN call gets the process, a DURABLE call the file), a `durable` fake serves only DURABLE and
  refuses a RUN call before any change (it has no RUN form, V-10.2).

Knobs (the leaf's fixtures use them): `lag_polls` unsatisfied readiness checks before a marker is
ready, `never_ready` keeps the postcondition false past `max_wait` (J-15, J-20, J-23a),
`outcome_codes` are the codes an unsatisfied check reports, one per confirmed repair (restart or
recreate): the trigger code is `outcome_codes[0]`, and `fixed_fingerprint` keeps it after a
confirmed repair (V-3.1's diagnostic fingerprint, read by J-3a). A repair also restarts the lag.

Creation is idempotent per run-scoped selector (B3-C4): a second `create` for the same
`(lineage, effect)` changes nothing and returns the same identity. The selector depends only on
`(lineage, effect)`, so every attempt carries an equal descriptor (V-10.1).
"""

from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
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

_HOLDER = (
    "import sys; sys.stdin.read()"  # a child that lives until its stdin closes or it is killed
)
FOUND_MAX = 16


@dataclass(frozen=True, slots=True)
class CheckResult:
    satisfied: bool
    code: str | None
    detail: str


@dataclass(frozen=True, slots=True)
class FoundRef:
    resource_kind: str
    selector: str
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class SelectorRef:
    lineage: Any
    effect: str
    selector: str
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class ResourceObservation:
    selector_present: bool
    selector_ref: SelectorRef | None
    identity_proven: bool
    configuration_compatible: bool
    currency: tuple[Any, ...]
    found: tuple[FoundRef, ...]
    code: str | None


@dataclass(frozen=True, slots=True)
class Endpoint:
    scheme: str
    host: str
    port: int


@dataclass(frozen=True, slots=True)
class RouteRefused:
    code: str
    human_action: str


def run_scoped_selector(lineage: Any, effect: str) -> str:
    """Depends only on `(lineage, effect)` (V-10.1)."""
    path = "/".join(lineage.path.segments)
    digest = hashlib.sha256(f"{lineage.root_run_id}\0{path}\0{effect}".encode()).hexdigest()
    return f"fake-{digest[:16]}"


def _process_token(pid: int) -> str | None:
    """The process's start token, or None if it is gone (a zombie is gone)."""
    proc = subprocess.run(
        ["ps", "-o", "stat=,lstart=", "-p", str(pid)], capture_output=True, text=True, check=False
    )
    line = proc.stdout.strip()
    if not line or line.startswith("Z"):
        return None
    return line.split(None, 1)[1] if " " in line else line


class FakeMarker:
    """`ResourceReads` + `ResourceCreate` + `ResourceOwned` over a marker directory."""

    def __init__(
        self,
        root: Path,
        lifetime: Any = "run",
        lag_polls: int = 0,
        never_ready: bool = False,
        fixed_fingerprint: bool = False,
        outcome_codes: Sequence[str] = (),
        *,
        owner: str = "environment",
    ) -> None:
        if _value(lifetime) not in ("run", "durable"):
            raise ValueError(f"lifetime must be run or durable, got {lifetime!r}")
        self.root = Path(root)
        self.lifetime = _value(lifetime)
        self.lag_polls = lag_polls
        self.never_ready = never_ready
        self.fixed_fingerprint = fixed_fingerprint
        self.outcome_codes = tuple(outcome_codes)
        self.owner = _value(owner)
        self.root.mkdir(parents=True, exist_ok=True)
        self._children: dict[str, subprocess.Popen[bytes]] = {}
        self._polls: dict[str, int] = {}
        self._repairs: dict[str, int] = {}

    # ------------------------------------------------------------------ lifecycle of the fake

    def close(self) -> None:
        """Kill every process this fake started (each by its own handle)."""
        for child in self._children.values():
            child.kill()
            child.wait()
            if child.stdin is not None:
                child.stdin.close()
        self._children.clear()

    def __enter__(self) -> FakeMarker:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def plant_found(self, logical_system: str, name: str = "pre-existing") -> str:
        """A marker that was not made by this root's selector (a found instance): a file only."""
        selector = f"found-{name}"
        self._write(selector, {"kind": "file", "system": logical_system, "found": True})
        return selector

    def inventory(self) -> dict[str, frozenset[str]]:
        """What exists, by category, for the suite's engine-inventory reach (B3-C17)."""
        present = frozenset(s for s in self._selectors() if self._record(s) is not None)
        empty: frozenset[str] = frozenset()
        return {"containers": present, "images": empty, "volumes": empty, "networks": empty}

    # ------------------------------------------------------------------ ResourceReads

    def observe(self, spec: Any, lineage: Any, effect: str | None) -> ResourceObservation:
        selector = None if effect is None else run_scoped_selector(lineage, effect)
        record = None if selector is None else self._record(selector)
        now = datetime.now(UTC)
        found = tuple(
            FoundRef(spec.logical_system, other, now)
            for other in sorted(self._selectors())
            if other != selector
            and self._record(other) is not None
            and self._read(other).get("system") == spec.logical_system
        )[:FOUND_MAX]
        ref = (
            SelectorRef(lineage, effect, selector, now)
            if record is not None and selector is not None and effect is not None
            else None
        )
        return ResourceObservation(
            selector_present=record is not None,
            selector_ref=ref,
            identity_proven=record is not None or bool(found),
            configuration_compatible=True,
            currency=(),
            found=found,
            code=None,
        )

    def check(self, check: str, target: Any) -> CheckResult:
        selector = target.selector
        if self._record(selector) is None:
            return CheckResult(False, None, f"{selector} is not present")
        polls = self._polls.get(selector, 0) + 1
        self._polls[selector] = polls
        ready = not self.never_ready and polls > self.lag_polls
        code = self._code(selector)
        if ready:
            return CheckResult(True, None, f"{check} holds")
        return CheckResult(False, code, f"{check} not yet")

    def endpoint(self, target: Any, vantage: Any) -> Endpoint | RouteRefused:
        if _value(vantage) != "host":
            return RouteRefused(ROUTE_UNSUPPORTED, "A marker has no route from a container.")
        port = 20000 + int(hashlib.sha256(target.selector.encode()).hexdigest()[:4], 16) % 20000
        return Endpoint("http", "127.0.0.1", port)

    # ------------------------------------------------------------------ ResourceCreate

    def launch_policy(self, spec: Any) -> ExecutionPolicy:
        return ExecutionPolicy(SelfProvisioning.DISABLED_BY_CONFIGURATION, Helpers.PREVENTED, None)

    def release_descriptor(self, call: Any) -> dict[str, Any]:
        if call.member == "create":
            if self._durable_kind(call.arguments["spec"], call.lifetime):
                return durable(self.owner)
            if self.lifetime == "durable":
                raise ValueError("a file-backed marker has no RUN release form (V-10.2)")
            helpers = self.launch_policy(call.arguments["spec"]).helpers
            return in_run_group(helpers is Helpers.DISCLOSED)
        # restart, recreate, stop: the handle's own descriptor, unchanged (V-5.4 item 2)
        return call.arguments["target"].release

    def create(self, spec: Any, ticket: Any) -> Confirmation:
        selector = run_scoped_selector(ticket.lineage, ticket.effect)
        as_file = self._durable_kind(spec, ticket.lifetime)
        if not as_file and self.lifetime == "durable":
            raise ValueError("a file-backed marker has no RUN form (V-10.2)")
        if self._record(selector) is None:  # idempotent per selector (B3-C4)
            self._make(selector, spec.logical_system, as_file)
        return Confirmation(ConfirmationStatus.APPLIED, None, selector)

    # ------------------------------------------------------------------ ResourceOwned

    def restart(self, target: Any, ticket: Any) -> Confirmation:
        self._repaired(target.selector)
        if self._record(target.selector) is None:
            self._make(target.selector, self._read(target.selector).get("system", ""), False)
        return Confirmation(ConfirmationStatus.APPLIED, None, target.selector)

    def recreate(self, target: Any, ticket: Any) -> Confirmation:
        system = self._read(target.selector).get("system", "")
        was_file = self._read(target.selector).get("kind") == "file"
        self._remove(target.selector)
        self._make(target.selector, system, was_file)
        self._repaired(target.selector)
        return Confirmation(ConfirmationStatus.APPLIED, None, target.selector)

    def stop(self, target: Any, ticket: Any) -> Confirmation:
        self._remove(target.selector)
        return Confirmation(ConfirmationStatus.APPLIED, None, target.selector)

    # ------------------------------------------------------------------ internals

    def _durable_kind(self, spec: Any, lifetime: Any) -> bool:
        return _value(lifetime) == "durable" or _value(spec.realization) == "provisioned"

    def _code(self, selector: str) -> str | None:
        if not self.outcome_codes:
            return None
        repairs = 0 if self.fixed_fingerprint else self._repairs.get(selector, 0)
        return self.outcome_codes[repairs] if repairs < len(self.outcome_codes) else None

    def _repaired(self, selector: str) -> None:
        self._repairs[selector] = self._repairs.get(selector, 0) + 1
        self._polls[selector] = 0  # the lag starts again after a repair

    def _path(self, selector: str) -> Path:
        return self.root / f"{selector}.marker"

    def _selectors(self) -> list[str]:
        return [p.name.removesuffix(".marker") for p in self.root.glob("*.marker")]

    def _read(self, selector: str) -> dict[str, Any]:
        try:
            data: dict[str, Any] = json.loads(self._path(selector).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data

    def _write(self, selector: str, record: dict[str, Any]) -> None:
        self._path(selector).write_text(json.dumps(record), encoding="utf-8")

    def _record(self, selector: str) -> dict[str, Any] | None:
        """The marker if it is observable: a file exists; a process is alive with its token."""
        record = self._read(selector)
        if not record:
            return None
        if record.get("kind") == "process":
            if _process_token(record["pid"]) != record["token"]:
                return None
        return record

    def _make(self, selector: str, system: str, as_file: bool) -> None:
        if as_file:
            self._write(selector, {"kind": "file", "system": system})
            return
        # stdin is a pipe this fake holds open: the child lives until closed or killed. It is
        # started in the caller's process group, so the run's group ends it (V-2.3).
        child = subprocess.Popen(
            [sys.executable, "-c", _HOLDER],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        token = _process_token(child.pid)
        self._children[selector] = child
        self._write(
            selector,
            {"kind": "process", "system": system, "pid": child.pid, "token": token},
        )

    def _remove(self, selector: str) -> None:
        record = self._read(selector)
        if record.get("kind") == "process":
            pid = record["pid"]
            if _process_token(pid) == record["token"]:  # never a reused pid
                os.kill(pid, signal.SIGKILL)
            child = self._children.pop(selector, None)
            if child is not None:
                child.wait()
                if child.stdin is not None:
                    child.stdin.close()
        self._path(selector).unlink(missing_ok=True)
