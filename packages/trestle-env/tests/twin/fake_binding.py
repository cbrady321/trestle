"""The twins' fake binding (AMB-2; owned by L.RB-0.4): the reference plugin, unmodified, on a fake
Docker engine.

`trestle_env.plugins._bind.reference_ports` hands the port map to `run_tree`; when the operator
environment names `TRESTLE_ENV_PORTS=module:callable` it returns that callable's map instead
(L.RB-0.3's seam). Every CI twin of a Slice B HOST node runs the SAME published plugin, the same
tree and the same MCP call as its HOST node, with one of the callables below as the seam:

* `fake_ports` - a healthy fake engine: every declared exec readiness check holds once its
  container runs with the credentials the check presents;
* `wrong_password_ports` - the same engine, but the Postgres readiness check presents a planted
  wrong password, so it never holds (the fake's Postgres refuses it, as the real one must, KDD 2);
* `real_wrong_password_ports` - NOT a fake: the real binding (`_bind.reference_ports`) with that
  same planted password, for the HOST node the `wrong_password_ports` twin mirrors.

The engine is `trestle_packs.fakes.FakeContainerEngine` (the stdlib fake the container family's
conformance suite and `differ d8 --pair container` hold equal to the real adapter), extended with
the reference tree's declared exec checks. A run's plugin process is not the test's, so the engine
keeps its state in the JSON file `TRESTLE_ENV_FAKE_STATE` names: loaded when the port map is built
(a test plants found containers there first) and rewritten after every call, with the call log a
twin asserts on (the order of creates, checks and stops, and what exists after the answer).

When the environment names a Compose definition (`TRESTLE_ENV_COMPOSE_FILE`, the variable the real
binding reads), the fake `FakeComposeResolver` over it is bound too, so the plugin derives the
closure of a twin's request exactly where it derives a HOST node's (L.RB-1.4).

Nothing here starts a process or reaches an engine: the twin is STUB · CI.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from trestle.workflow import ports
from trestle_packs.fakes.command import Confirmation, ConfirmationStatus
from trestle_packs.fakes.compose import FakeComposeResolver
from trestle_packs.fakes.container import FakeContainerEngine, _Container, _host_port
from trestle_packs.fakes.provision import FakeProvision

from trestle_env import tree
from trestle_env.plugins import _bind
from trestle_env.plugins._route import RealizationRouter

STATE_ENV = "TRESTLE_ENV_FAKE_STATE"
SEAM = f"{__name__}:fake_ports"
WRONG_PASSWORD_SEAM = f"{__name__}:wrong_password_ports"
REAL_WRONG_PASSWORD_SEAM = f"{__name__}:real_wrong_password_ports"
WRONG_PASSWORD = "planted-wrong-password"
FAKE_DOCKER = "/fake/bin/docker"
FAKE_ENDPOINT = "unix:///fake/desktop-linux.sock"
# L.RB-12.3/12.5: the executable the fake's `ArgvRelease` descriptors name (a release wrapper over
# `twin/state_docker.py`, so the host sweep can release against this same state), and a planted
# release failure (`remove`: the fake's own stop leaves the container, stopped, and answers
# UNKNOWN, as the real adapter does when `rm` fails)
DOCKER_ENV = "TRESTLE_ENV_FAKE_DOCKER"
FAIL_ENV = "TRESTLE_ENV_FAKE_FAIL"


class FakeReferenceEngine(FakeContainerEngine):
    """The fake engine with the tree's declared exec checks and a durable state file."""

    def __init__(
        self,
        state_path: Path | None,
        checks: Mapping[str, Mapping[str, str]],
        executable: str = FAKE_DOCKER,
        fail: str | None = None,
    ) -> None:
        super().__init__(executable=executable, endpoint=FAKE_ENDPOINT)
        self._fail = fail
        self._state_path = state_path
        self._checks = {name: dict(env) for name, env in checks.items()}
        self.calls: list[dict[str, Any]] = []
        self.environment: dict[str, dict[str, str]] = {}  # selector -> the container's env
        self._lock = threading.RLock()  # sibling nodes run concurrently in one process
        self._load()

    # ---- durable state (the plugin runs in another process)

    def _load(self) -> None:
        if self._state_path is None or not self._state_path.exists():
            return
        data = json.loads(self._state_path.read_text(encoding="utf-8"))
        for row in data.get("containers", []):
            name = row["name"]
            self._containers[name] = _Container(
                name, row.get("state", "running"), row.get("port", 0), _host_port(name)
            )
            self.environment[name] = dict(row.get("environment", {}))
        self._created = set(data.get("created", []))
        self._volumes = set(data.get("volumes", []))
        self.calls = list(data.get("calls", []))

    def _save(self) -> None:
        if self._state_path is None:
            return
        with self._lock:
            self._write()

    def _write(self) -> None:
        assert self._state_path is not None
        data = {
            "containers": [
                {
                    "name": c.name,
                    "state": c.state,
                    "port": c.container_port,
                    "environment": self.environment.get(c.name, {}),
                }
                for c in self._containers.values()
            ],
            "created": sorted(self._created),
            "volumes": sorted(self._volumes),
            "calls": self.calls,
        }
        tmp = self._state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        os.replace(tmp, self._state_path)

    def _log(self, member: str, selector: str | None, **extra: Any) -> None:
        with self._lock:
            self.calls.append({"member": member, "selector": selector, **extra})
            self._save()

    # ---- the port members a run reaches (each logged, state saved)

    def observe(self, spec: Any, lineage: Any, effect: str | None) -> Any:
        seen = super().observe(spec, lineage, effect)
        self._log("observe", None, present=seen.selector_present, found=len(seen.found))
        return seen

    def check(self, check: str, target: Any) -> Any:
        expected = self._checks.get(check)
        if check in tree.HTTP_READINESS:
            # a declared HTTP contract: the fake container serves the declared response once it
            # runs (the real binding reads it through `HttpReadinessReads`, L.RB-2.1)
            base = super().check("running", target)
            answer = type(base)(base.satisfied, base.code, f"{check}: {base.detail}")
        elif expected is None:
            answer = super().check(check, target)
        else:
            answer = self._exec_check(check, target, expected)
        self._log("check", target.selector, check=check, satisfied=answer.satisfied)
        return answer

    def _exec_check(self, check: str, target: Any, presented: Mapping[str, str]) -> Any:
        down = self._down()
        base = super().check("running", target)
        if down is not None or not base.satisfied:
            return base
        wanted = self.environment.get(target.selector, {}).get("POSTGRES_PASSWORD")
        if wanted is not None and presented.get("PGPASSWORD") != wanted:
            # the real adapter's words for a failing exec check (reads.py)
            refused = f'password authentication failed for user "{tree.POSTGRES_USER}"'
            detail = f"{check} not yet: psql: FATAL:  {refused}"
            return type(base)(False, None, detail)
        return type(base)(True, None, f"{check} holds")

    def create(self, spec: Any, ticket: Any) -> Any:
        answer = super().create(spec, ticket)
        selector = answer.identity
        if selector is not None and spec.logical_system == tree.POSTGRES_SERVICE:
            self._containers[selector].container_port = _bind.POSTGRES_PORT
            self.environment[selector] = {"POSTGRES_PASSWORD": tree.POSTGRES_FIXTURE_PASSWORD}
        self._log("create", selector, status=_status(answer))
        return answer

    def restart(self, target: Any, ticket: Any) -> Any:
        answer = super().restart(target, ticket)
        self._log("restart", target.selector, status=_status(answer))
        return answer

    def recreate(self, target: Any, ticket: Any) -> Any:
        answer = super().recreate(target, ticket)
        self._log("recreate", target.selector, status=_status(answer))
        return answer

    def stop(self, target: Any, ticket: Any) -> Any:
        if self._fail == "remove" and target.selector in self._containers:
            with self._lock:
                self._containers[target.selector].state = "exited"  # stopped, never removed
            answer = Confirmation(ConfirmationStatus.UNKNOWN, None, target.selector)
            self._log("stop", target.selector, status=_status(answer))
            return answer
        answer = super().stop(target, ticket)
        self.environment.pop(target.selector, None)
        self._log("stop", target.selector, status=_status(answer))
        return answer

    def start(self, target: Any, ticket: Any) -> Any:
        answer = super().start(target, ticket)
        self._log("start", target.selector, status=_status(answer))
        return answer

    def run_argv(self, argv: Any) -> tuple[int, str]:
        done = super().run_argv(argv)
        self._log("argv", None, argv=list(argv), exit=done[0])
        return done


def _status(answer: Any) -> str:
    status = getattr(answer, "status", None)
    return str(getattr(status, "value", status))


def _checks(readiness_environment: Mapping[str, str] | None) -> dict[str, dict[str, str]]:
    """The tree's declared exec checks and the environment each presents (`_bind.exec_checks`)."""
    bound = _bind.exec_checks(readiness_environment)
    return {name: dict(check.environment) for name, check in bound.items()}


RECORDS_ENV = "TRESTLE_ENV_FAKE_RECORDS"


class DurableFakeProvision(FakeProvision):
    """`FakeProvision` whose records and submit count persist to the JSON file `RECORDS_ENV`
    names: the fake's stand-in for an environment store that outlives a run (the real binding's
    `TRESTLE_ENV_RECORD_STORE`, L.RB-6.3). Without a file it is the in-memory fake."""

    def __init__(self, path: Path | None) -> None:
        super().__init__()
        self._path = path
        if path is not None and path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            self.records = {k: (v[0], v[1]) for k, v in data.get("records", {}).items()}
            self.submits = int(data.get("submits", 0))

    def create(self, spec: Any, ticket: Any) -> Any:
        answer = super().create(spec, ticket)
        if self._path is not None:
            data = {"records": {k: list(v) for k, v in self.records.items()}}
            data["submits"] = self.submits  # type: ignore[assignment]
            self._path.write_text(json.dumps(data), encoding="utf-8")
        return answer


def read_records(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"records": {}, "submits": 0}
    loaded: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return loaded


def _ports(
    environ: Mapping[str, str],
    readiness_environment: Mapping[str, str] | None,
    *,
    reachable: bool = True,
) -> Mapping[type, object]:
    state = environ.get(STATE_ENV)
    engine = FakeReferenceEngine(
        Path(state) if state else None,
        _checks(readiness_environment),
        executable=environ.get(DOCKER_ENV) or FAKE_DOCKER,
        fail=environ.get(FAIL_ENV) or None,
    )
    engine.reachable = reachable
    mapping: dict[type, object] = {
        ports.ResourceReads: engine,
        ports.ResourceCreate: engine,
        ports.ResourceOwned: engine,
        ports.ResourceSafeStart: engine,
    }
    if tree.PROVISION_UNIT in tree.ENTRY.units:
        # the tree provisions a fixture record (L.RB-6.3): its `PROVISIONED` resource goes to the
        # fake record store, routed as the real binding routes it
        records = environ.get(RECORDS_ENV)
        store = DurableFakeProvision(Path(records) if records else None)
        router = RealizationRouter(engine, store)
        mapping[ports.ResourceReads] = router
        mapping[ports.ResourceCreate] = router
    compose = environ.get(_bind.COMPOSE_ENV)
    if compose:
        # the same operator variable the real binding reads (L.RB-1.4): the closure is derived
        # by the fake resolver over that definition (it must be written in JSON syntax)
        mapping[ports.ComposeResolver] = FakeComposeResolver(
            {_bind.REFERENCE_COMPOSE_PROJECT: compose}
        )
    return mapping


def fake_ports(environ: Mapping[str, str]) -> Mapping[type, object]:
    """The seam: a healthy fake engine (`TRESTLE_ENV_PORTS=<SEAM>`)."""
    return _ports(environ, None)


def wrong_password_ports(environ: Mapping[str, str]) -> Mapping[type, object]:
    """The seam with a planted wrong Postgres password in the readiness check's environment."""
    return _ports(environ, {"PGPASSWORD": WRONG_PASSWORD})


UNREACHABLE_SEAM = f"{__name__}:unreachable_ports"


def unreachable_ports(environ: Mapping[str, str]) -> Mapping[type, object]:
    """The seam bound to an engine that does not answer (L.RB-7.1): every docker call exits as
    the real CLI does at an absent endpoint (`DOCKER_ENGINE_UNREACHABLE`, never
    `DOCKER_CLI_MISSING`)."""
    return _ports(environ, None, reachable=False)


class RecordingExecution:
    """The real `CommandPort`, with every argv it runs appended to the JSON-lines file
    `TRESTLE_ENV_ARGV_LOG` names (what a HOST node asserts about the commands the adapter ran)."""

    def __init__(self, log: Path | None) -> None:
        from trestle_packs.process.command import CommandPort

        self._inner = CommandPort()
        self._log = log

    def policy(self, command: Any) -> Any:
        return self._inner.policy(command)

    def release_descriptor(self, call: Any) -> Any:
        return self._inner.release_descriptor(call)

    def run(self, command: Any, ticket: Any, cancel: Any, until: Any) -> Any:
        if self._log is not None:
            with self._log.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(list(command.argv)) + "\n")
        return self._inner.run(command, ticket, cancel, until)


ARGV_LOG_ENV = "TRESTLE_ENV_ARGV_LOG"


def real_wrong_password_ports(environ: Mapping[str, str]) -> Mapping[type, object]:
    """The REAL binding with the planted wrong password in the Postgres readiness environment,
    its commands recorded (`TRESTLE_ENV_ARGV_LOG`)."""
    real = {k: v for k, v in environ.items() if k != _bind.PORTS_ENV}
    log = environ.get(ARGV_LOG_ENV)
    return _bind.reference_ports(
        real,
        execution=RecordingExecution(Path(log) if log else None),  # type: ignore[arg-type]
        readiness_environment={"PGPASSWORD": WRONG_PASSWORD},
    )


def read_argv_log(path: Path) -> list[list[str]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def read_state(path: Path) -> dict[str, Any]:
    """What the fake engine holds after a run, and every call it answered."""
    if not path.exists():
        return {"containers": [], "created": [], "volumes": [], "calls": []}
    loaded: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return loaded
