"""HOST: a root cancel during readiness reaches an owned process, a created container and a found
container, each with its own disposition (L.RB-10.1; B6.1, WR-CANCEL-4, WR-OWN-2; DOCKER+PROC,
run only by the host-docker gate).

The test plugin `fixtures/cancel_readiness.py` (DEVIATION: the reference tree has no owned process
during readiness, so the three kinds are put side by side on a tree of the reference units) runs on
the reference binding over the operator's docker at the gate's endpoint with the pinned images:
`backend.http_support` is a container the run creates, `backend.postgres` reuses the pre-started
fixture Postgres (`fixtures/reuse-postgres`, container `postgres`, seeded row), and `backend.app`
is the stdlib app in `never` mode, a local process the run owns. The cancel lands while the app's
readiness is awaited (`twin.cancel_case`): the owned process is gone within the stop bound, the
created container is absent afterwards (released: observe, stop, remove, observe), the found one is
exactly as it was, and past the stop row only releases were issued. Twin:
`twin/test_cancel_readiness_twin.py` (same node name).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from tests.proof import tolerances
from twin import cancel_case, harness

from trestle_env import tree
from trestle_env.plugins import _bind

pytestmark = pytest.mark.docker_host

COMPOSE = Path(__file__).resolve().parents[1] / "fixtures" / "reuse-postgres" / "compose.yaml"
PROJECT = "reuse-postgres"
FOUND = tree.POSTGRES_SERVICE


def docker(*args: str, check: bool = True) -> str:
    cli = shutil.which("docker")  # the operator's path, named here; the adapter never searches
    assert cli is not None, "the host-docker gate runs with a docker CLI (preflight)"
    done = subprocess.run(  # noqa: S603 - the test's own fixture
        [cli, "--host", os.environ[_bind.ENDPOINT_ENV], *args],
        capture_output=True,
        text=True,
        check=check,
        stdin=subprocess.DEVNULL,
        timeout=tolerances.JOIN_WAIT_S * 12,
    )
    return done.stdout.strip()


def sql(statement: str) -> str:
    return docker(
        "exec", FOUND, "psql", "-U", tree.POSTGRES_USER, "-d", tree.POSTGRES_DATABASE,
        "-tAc", statement,
    )  # fmt: skip


@pytest.fixture
def prestarted() -> Iterator[None]:
    """The found Postgres: started, answering, one seeded row; removed with its volume after."""
    docker("compose", "-p", PROJECT, "-f", str(COMPOSE), "up", "-d")
    try:
        deadline = time.monotonic() + tolerances.JOIN_WAIT_S * 6
        while True:
            try:
                sql("SELECT 1")
                break
            except subprocess.CalledProcessError:
                assert time.monotonic() < deadline, "the fixture Postgres never answered"
                time.sleep(tolerances.POLL_S)
        sql("CREATE TABLE IF NOT EXISTS seed (k text PRIMARY KEY, v text)")
        sql("INSERT INTO seed VALUES ('row', 'seeded') ON CONFLICT (k) DO NOTHING")
        yield
    finally:
        docker("compose", "-p", PROJECT, "-f", str(COMPOSE), "down", "-v", check=False)


def snapshot() -> dict[str, Any]:
    inspected = json.loads(docker("inspect", FOUND))[0]
    return {
        "id": inspected["Id"],
        "running": inspected["State"]["Running"],
        "started_at": inspected["State"]["StartedAt"],
        "row": sql("SELECT v FROM seed WHERE k = 'row'"),
    }


@pytest.mark.proves(
    "WR-CANCEL-4", "WR-CANCEL-4:b-created-gone-found-untouched", "B", "B", "DOCKER+PROC", "HOST"
)
@pytest.mark.proves("WR-OWN-2", "WR-OWN-2:b-cancel-path", "B", "B", "DOCKER+PROC", "HOST")
@pytest.mark.proves("WR-CANCEL-4", "B6.1", "B", "B", "DOCKER+PROC", "HOST")
def test_cancel_during_readiness_owned_and_created_gone_found_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, prestarted: None
) -> None:
    docker_path = shutil.which("docker")
    assert docker_path is not None
    environ, log = cancel_case.app_environ(tmp_path)
    for name, value in environ.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv(_bind.DOCKER_PATH_ENV, docker_path)
    monkeypatch.delenv(_bind.PORTS_ENV, raising=False)
    monkeypatch.setenv("PYTHONPATH", harness.plugin_pythonpath())
    before = snapshot()
    kernel = cancel_case.kernel(tmp_path)
    done, elapsed, port = cancel_case.cancel_mid_readiness(kernel, "cancel-host", log)
    cancel_case.assert_cancel_facts(kernel, done, elapsed, log, port, FOUND)
    left = docker("ps", "-a", "--format", "{{.Names}}", "--filter", f"name=trwr-{done.run_id}-")
    assert left == "", left  # the created container: observed absent
    assert snapshot() == before  # the found one: exactly as it was
