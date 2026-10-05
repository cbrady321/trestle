"""A pre-started Postgres is reused and left untouched on every path, on real Docker (L.RB-3.1;
B8.1, WR-OWN-1, WR-OWN-2, WR-OWN-10).

DOCKER, venue HOST (`docker_host`; `docker_gate run`, F-PX3). The TEST starts the fixture
(`tests/fixtures/reuse-postgres/compose.yaml`: a Postgres named as the logical system, on the pinned
image, with its data on a named volume the test seeds with a row) BEFORE the run, so it is a found
resource, never run-scoped, attributable to the gate's housekeeping by its fixture label. The
published `reference_env` then runs through the control surface on three paths:

* passed: the found Postgres is reported REUSED (its identity and configuration read from inside it,
  its authenticated readiness passing) and the supporting service STARTED;
* failed: the supporting service can never become ready (`planted_support.py`), the run is blocked;
* cancelled: the same, and the run is cancelled while it waits.

On every path the fixture container's id, image, environment, start time, running state, its volume
and the seeded row are exactly what they were, and only what the run created is gone. CI twin:
`tests/twin/test_reuse_twin.py`."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from tests.proof import tolerances
from trestle.common import clock
from trestle.common.types import RunView
from trestle.server.main import Kernel, create_kernel

from trestle_env import schema, tree
from trestle_env.plugins import _bind, reference_env

pytestmark = pytest.mark.docker_host

PLUGINS = Path(reference_env.__file__).resolve().parent
COMPOSE = Path(__file__).resolve().parents[1] / "fixtures" / "reuse-postgres" / "compose.yaml"
NEVER_READY = Path(__file__).with_name("planted_support.py")
PROJECT = "reuse-postgres"
FOUND = tree.POSTGRES_SERVICE


def docker(*args: str) -> str:
    cli = shutil.which("docker")  # the operator's path, named here; the adapter never searches
    assert cli is not None, "the host-docker gate runs with a docker CLI (preflight)"
    host = ["--host", os.environ[_bind.ENDPOINT_ENV]] if os.environ.get(_bind.ENDPOINT_ENV) else []
    done = subprocess.run(  # noqa: S603 - the test's own fixture
        [cli, *host, *args], capture_output=True, text=True, check=True, timeout=120
    )
    return done.stdout.strip()


def sql(statement: str) -> str:
    return docker(
        "exec",
        FOUND,
        "psql",
        "-U",
        tree.POSTGRES_USER,
        "-d",
        tree.POSTGRES_DATABASE,
        "-tAc",
        statement,
    )


@pytest.fixture
def prestarted() -> Iterator[None]:
    """Start the fixture Postgres, wait until it answers, seed a row; remove what it made after."""
    docker("compose", "-p", PROJECT, "-f", str(COMPOSE), "up", "-d")
    try:
        deadline = time.monotonic() + tolerances.JOIN_WAIT_S * 6
        while True:
            try:
                sql("SELECT 1")
                break
            except subprocess.CalledProcessError:
                assert time.monotonic() < deadline, "the fixture Postgres never answered"
                time.sleep(tolerances.POLL_FINE_S * 10)
        sql("CREATE TABLE IF NOT EXISTS seed (k text PRIMARY KEY, v text)")
        sql("INSERT INTO seed VALUES ('row', 'seeded') ON CONFLICT (k) DO NOTHING")
        yield
    finally:
        # the fixture's own, labelled objects: the test that made the volume removes it
        subprocess.run(  # noqa: S603
            [
                shutil.which("docker") or "docker",
                "compose",
                "-p",
                PROJECT,
                "-f",
                str(COMPOSE),
                "down",
                "-v",
            ],
            capture_output=True,
            check=False,
            timeout=120,
        )


def snapshot() -> dict[str, Any]:
    """Everything a run must leave as it found it."""
    inspected = json.loads(docker("inspect", FOUND))[0]
    volume = json.loads(docker("volume", "inspect", f"{PROJECT}_reuse_data"))[0]
    return {
        "id": inspected["Id"],
        "running": inspected["State"]["Running"],
        "started_at": inspected["State"]["StartedAt"],
        "image": inspected["Config"]["Image"],
        "env": sorted(inspected["Config"]["Env"]),
        "volume": (volume["Name"], volume["CreatedAt"]),
        "row": sql("SELECT v FROM seed WHERE k = 'row'"),
    }


@pytest.fixture
def kernel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Kernel:
    docker_path = shutil.which("docker")
    assert docker_path is not None
    monkeypatch.setenv(_bind.DOCKER_PATH_ENV, docker_path)
    monkeypatch.setenv("TRESTLE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    built = create_kernel(home=tmp_path / "home", plugin_dirs=[PLUGINS], skip_recovery=True)
    built.registry.refresh()
    return built


def dispositions(answer: dict[str, Any]) -> dict[str, str | None]:
    nodes = [answer["primary"], *answer.get("listed", [])]
    return {"/".join(n["path"]): n["disposition"] for n in nodes if n["path"]}


def wait_created(run_id: str) -> None:
    """Until the run has created its supporting container (so a cancel lands mid-wait)."""
    name = f"trwr-{run_id}-{tree.HTTP_SUPPORT_PATH.replace('/', '.')}"
    deadline = time.monotonic() + tolerances.JOIN_WAIT_S * 3
    while time.monotonic() < deadline:
        if docker("ps", "-a", "--format", "{{.Names}}", "--filter", f"name=^/{name}$"):
            return
        time.sleep(tolerances.POLL_FINE_S)
    raise AssertionError(f"{name} was never created")


@pytest.mark.proves("WR-OWN-1", "B8.1", "B", "B", "DOCKER", "HOST")
@pytest.mark.proves(
    "WR-OWN-1", "WR-OWN-1:b-dispositions-reused-started", "B", "B", "DOCKER", "HOST"
)
@pytest.mark.proves("WR-OWN-2", "WR-OWN-2:b-found-untouched-all-paths", "B", "B", "DOCKER", "HOST")
@pytest.mark.proves("WR-OWN-10", "WR-OWN-10:b-cleanup-never-found", "B", "B", "DOCKER", "HOST")
@pytest.mark.parametrize("path", ["passed", "failed", "cancelled"])
def test_prestarted_postgres_reused_untouched(
    kernel: Kernel, prestarted: None, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    if path != "passed":
        monkeypatch.setenv(_bind.PORTS_ENV, f"{NEVER_READY}:never_ready")
    before = snapshot()
    args = {schema.ENV_ARG: f"reuse-{uuid.uuid4().hex[:8]}"}
    wait_ms = int((tree.DEADLINE_S + clock.finalization_margin) * 1000)
    if path == "cancelled":
        started = kernel.control.run(plugin="reference_env", args=args, wait_ms=0)
        assert isinstance(started, RunView), started
        wait_created(started.run_id)
        kernel.control.cancel(started.run_id)
        done = kernel.control.project.await_terminal(started.run_id)
    else:
        done = kernel.control.run(
            plugin="reference_env", args=args, wait_ms=wait_ms, completion="terminal"
        )
    assert isinstance(done, RunView), done
    answer = done.answer
    assert answer is not None, done
    if path == "passed":
        assert answer["outcome"] == "passed", answer
        shown = dispositions(answer)
        assert shown[tree.POSTGRES_SERVICE] == "reused"
        assert shown[tree.HTTP_SUPPORT_PATH] == "started"
    elif path == "failed":
        assert answer["outcome"] == "blocked", answer
    else:
        assert answer["root_stop"] == "cancel", answer
    # the found resource is exactly what it was: never signalled, adopted, restarted or released
    assert snapshot() == before
    # what the run created is gone
    left = docker("ps", "-a", "--format", "{{.Names}}", "--filter", f"name=trwr-{done.run_id}-")
    assert left == ""
