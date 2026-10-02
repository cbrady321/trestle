"""Bindings of the provisioning family (L.RB-6.1): the stdlib fake, the real `ProvisionPort` over
the real `CommandPort` and the `psql_double` (no engine: `real-shim`), and the real adapter over the
operator's docker and a real Postgres container (`real`, the host-docker gate, `docker_host`).

The family's cases (`tests/conformance/provision_cases.py`) run UNMODIFIED against all three; what
they need beyond the port is the fixture contract in that module's docstring, built here.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from tests.proof import tolerances
from tests.proof.host.docker_gate import inventory
from tests.proof.suites.ports import core
from trestle.workflow import ports
from trestle.workflow.declarations import RealizationKind

from trestle_packs.container.engine import DockerCli
from trestle_packs.fakes.provision import FakeProvision
from trestle_packs.process.command import CommandPort
from trestle_packs.provision import TABLE, ProvisionPort, RecordStore, psql_args

HERE = Path(__file__).resolve().parent
ENDPOINT = "unix:///fake/desktop-linux.sock"
STORE_CONTAINER = "suite-store"
ROLE, DATABASE, PASSWORD, WRONG_PASSWORD = "suite", "suite", "fixture-secret-pw", "not-the-password"
ENTRY = "suite-entry"
PAYLOADS = {ENTRY: "fixture-record"}
SYSTEM = "suite-records"
SPEC = ports.ResourceSpec(SYSTEM, RealizationKind.PROVISIONED, ENTRY, None)
FIXTURE_LABEL = "trestle.proof.fixture=provision-suite"
READY_WAIT_S = tolerances.JOIN_WAIT_S * 6  # a Postgres container: init, then its real server


def selector_of(lineage: Any) -> str:
    """MC-B-01, written out independently of the adapter: `trwr-<root run_id>-<path>`."""
    return f"trwr-{lineage.root_run_id}-{'.'.join(lineage.path.segments)}"


def store(password: str = PASSWORD, container: str = STORE_CONTAINER) -> RecordStore:
    return RecordStore(
        container=lambda lineage: container,
        role=ROLE,
        database=DATABASE,
        password=password,
        payloads=PAYLOADS,
    )


# ------------------------------------------------------------------------------- fake


def fake_provision(
    fake_class: type[FakeProvision] = FakeProvision, **knobs: Any
) -> Callable[[], core.Implementation]:
    def build() -> core.Implementation:
        fake = fake_class(**knobs)
        return core.Implementation(
            fake,
            core.Reach(engine_inventory=fake.inventory),
            name="fake",
            extras={
                "spec": SPEC,
                "secret": PASSWORD,
                "plant_found": fake.plant_found,
                "unreadable": lambda: core.Implementation(
                    fake_class(readable=False), name="fake-down"
                ),
            },
        )

    return build


# ------------------------------------------------------------------------------- real-shim


def install_double(directory: Path, state: Path, log: Path | None = None) -> str:
    """An absolute-path `docker` launcher for the psql double, bound to `state` (and `log`) by
    content: the adapters run docker with an environment built from empty."""
    launcher = Path(directory) / "docker"
    bindings = {"PSQL_DOUBLE_STATE": str(state)}
    if log is not None:
        bindings["PSQL_DOUBLE_LOG"] = str(log)
    launcher.write_text(
        f"#!{sys.executable}\n"
        "import os, runpy\n"
        f"os.environ.update({bindings!r})\n"
        f"runpy.run_path({str(HERE / 'psql_double.py')!r}, run_name='__main__')\n"
    )
    launcher.chmod(0o755)
    return str(launcher)


class Shim:
    """One double: its launcher, state file, sqlite store and call log."""

    def __init__(self, directory: Path, **state: object) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self.state_path, self.log = directory / "state.json", directory / "log.jsonl"
        self.db = directory / "store.db"
        self.state: dict[str, object] = {
            "container": STORE_CONTAINER,
            "password": PASSWORD,
            "role": ROLE,
            "database": DATABASE,
            "store": str(self.db),
            **state,
        }
        self.write()
        self.executable = install_double(directory, self.state_path, self.log)

    def write(self, **changes: object) -> None:
        self.state.update(changes)
        self.state_path.write_text(json.dumps(self.state), encoding="utf-8")

    def calls(self) -> list[dict[str, Any]]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines() if line.strip()]

    def keys(self) -> frozenset[str]:
        if not self.db.exists():
            return frozenset()
        connection = sqlite3.connect(self.db)
        try:
            return frozenset(r[0] for r in connection.execute(f"SELECT key FROM {TABLE}"))
        except sqlite3.OperationalError:
            return frozenset()
        finally:
            connection.close()

    def plant(self, system: str) -> str:
        key = f"trwr-r_other_root-planted-{len(self.keys())}"
        connection = sqlite3.connect(self.db)
        try:
            connection.execute(
                f"CREATE TABLE IF NOT EXISTS {TABLE} "
                "(key TEXT PRIMARY KEY, system TEXT NOT NULL, payload TEXT NOT NULL)"
            )
            connection.execute(f"INSERT INTO {TABLE} VALUES (?, ?, 'planted')", (key, system))
            connection.commit()
        finally:
            connection.close()
        return key


def port_over(shim: Shim, password: str = PASSWORD, cancel: Any = None) -> ProvisionPort:
    return ProvisionPort(
        DockerCli(shim.executable, ENDPOINT, CommandPort(), cancel), store(password)
    )


def real_shim_provision(base: Path) -> Callable[[], core.Implementation]:
    counter = iter(range(10_000))

    def build() -> core.Implementation:
        shim = Shim(base / f"store-{next(counter)}")
        return core.Implementation(
            port_over(shim),
            core.Reach(
                engine_inventory=lambda: {
                    "containers": frozenset({STORE_CONTAINER}),
                    "records": shim.keys(),
                }
            ),
            name="real-shim",
            extras={
                "spec": SPEC,
                "secret": PASSWORD,
                "plant_found": shim.plant,
                "unreadable": lambda: core.Implementation(
                    port_over(shim, WRONG_PASSWORD), name="real-shim-wrong-password"
                ),
            },
        )

    return build


# ------------------------------------------------------------------------------- real (HOST)


class RealStore:
    """A real Postgres container the suite plants and removes with plain docker calls (never with
    the adapter under test): fixture-labelled (CSC-10), data on tmpfs (no volume), image by role."""

    def __init__(self, cli: str, endpoint: str | None, image: str, name: str) -> None:
        self.cli, self.endpoint, self.image, self.name = cli, endpoint, image, name
        self.started = False

    def docker(self, *args: str) -> tuple[int, str]:
        host = ["--host", self.endpoint] if self.endpoint else []
        done = subprocess.run(  # noqa: S603 - the suite's own docker, an absolute path
            [self.cli, *host, *args],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            env=inventory.docker_env(),
            timeout=tolerances.JOIN_WAIT_S,
            check=False,
        )
        return done.returncode, done.stdout + done.stderr

    def psql(self, sql: str) -> tuple[int, str]:
        return self.docker(*psql_args(store(), self.name, sql))

    def start(self) -> None:
        if self.started:
            return
        code, out = self.docker(
            "run", "-d", "--pull", "never", "--name", self.name, "--label", FIXTURE_LABEL,
            "--tmpfs", "/var/lib/postgresql/data",
            "-e", f"POSTGRES_PASSWORD={PASSWORD}", "-e", f"POSTGRES_USER={ROLE}",
            "-e", f"POSTGRES_DB={DATABASE}", self.image,
        )  # fmt: skip
        assert code == 0, out
        self.started = True
        deadline = time.monotonic() + READY_WAIT_S
        while time.monotonic() < deadline:  # the authenticated TCP `SELECT 1`: init's temporary
            if self.psql("SELECT 1")[0] == 0:  # server listens on a socket only, so this waits
                return  # for the real one
            time.sleep(tolerances.POLL_S)
        raise AssertionError(f"{self.name} never answered an authenticated SELECT 1")

    def reset(self) -> None:
        self.start()
        code, out = self.psql(f"DROP TABLE IF EXISTS {TABLE}")
        assert code == 0, out

    def keys(self) -> frozenset[str]:
        code, out = self.psql(f"SELECT key FROM {TABLE}")
        return frozenset(out.split()) if code == 0 else frozenset()

    def plant(self, system: str) -> str:
        key = f"trwr-r_other_root-planted-{len(self.keys())}"
        code, out = self.psql(
            f"CREATE TABLE IF NOT EXISTS {TABLE} "
            "(key TEXT PRIMARY KEY, system TEXT NOT NULL, payload TEXT NOT NULL); "
            f"INSERT INTO {TABLE} VALUES ('{key}', '{system}', 'planted')"
        )
        assert code == 0, out
        return key

    def cleanup(self) -> None:
        if self.started:
            self.docker("stop", self.name)
            self.docker("rm", self.name)  # no `-v`, no `-f`: the data was on tmpfs
            self.started = False


class RealFactory:
    """`Callable[[], Implementation]` with a `cleanup()` that removes the one Postgres container
    (started once, its table dropped before each case)."""

    def __init__(self, cli: str, endpoint: str | None, image: str, name: str) -> None:
        self.engine = RealStore(cli, endpoint, image, name)
        self.cli, self.endpoint = cli, endpoint

    def __call__(self) -> core.Implementation:
        self.engine.reset()
        engine, cli, endpoint = self.engine, self.cli, self.endpoint

        def inventory_of() -> Mapping[str, frozenset[str]]:
            snap = inventory.snapshot(cli, endpoint)
            assert snap["engine"]["reachable"], snap["engine"]
            kinds = {
                kind: frozenset(
                    name
                    for obj in snap[kind]
                    for name in (
                        inventory.object_names(obj)
                        if kind != "images"
                        else [inventory._key(kind, obj)]
                    )
                )
                for kind in inventory.KINDS
            }
            return {**kinds, "records": engine.keys()}

        def bind(password: str) -> ProvisionPort:
            return ProvisionPort(
                DockerCli(cli, endpoint, CommandPort()), store(password, engine.name)
            )

        return core.Implementation(
            bind(PASSWORD),
            core.Reach(engine_inventory=inventory_of),
            name="real",
            extras={
                "spec": SPEC,
                "secret": PASSWORD,
                "plant_found": engine.plant,
                "unreadable": lambda: core.Implementation(
                    bind(WRONG_PASSWORD), name="real-wrong-password"
                ),
            },
        )

    def cleanup(self) -> None:
        self.engine.cleanup()


def real_docker_provision(_base: Path) -> RealFactory:
    cli = shutil.which("docker")  # the test names the operator's path; the adapter never searches
    assert cli is not None, "the host-docker gate runs with a docker CLI (preflight)"
    endpoint = os.environ.get("TRESTLE_DOCKER_ENDPOINT")
    image = os.environ["TRESTLE_IMAGE_POSTGRES"]  # `<repo>@sha256:<hex>`, exported by the gate
    return RealFactory(cli, endpoint, image, "suite-store")
