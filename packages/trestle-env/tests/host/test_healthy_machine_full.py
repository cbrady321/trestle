"""HOST: one call on a healthy machine ends with every disposition: the created container STARTED,
the found Postgres REUSED and untouched (its volume and seeded row exactly as they were), and an
owned app killed mid-readiness REPAIRED by one restart (L.RB-8.3; WR-ENV-10, B7.1; DOCKER+PROC,
run only by the host-docker gate).

The test plugin `fixtures/owned_restart.py` (DEVIATION: the reference service unit declares no
owned-restart remedy, so the app runs under a test-defined unit that does) runs on the reference
binding over the operator's docker at the gate's endpoint with the pinned images:
`backend.http_support` is a container the run creates, `backend.postgres` reuses the pre-started
fixture Postgres
(`fixtures/reuse-postgres`, container `postgres`, seeded row, named volume), and `backend.app` is
the stdlib app, a local process the run owns, SIGKILLed while the run waits for its readiness
(`twin.healthy_case`). Twin: `twin/test_healthy_machine_full_twin.py` (same node name).
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
from twin import harness, healthy_case

from trestle_env import tree
from trestle_env.plugins import _bind

pytestmark = pytest.mark.docker_host

COMPOSE = Path(__file__).resolve().parents[1] / "fixtures" / "reuse-postgres" / "compose.yaml"
PROJECT = "reuse-postgres"
VOLUME = f"{PROJECT}_reuse_data"
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
    volume = json.loads(docker("volume", "inspect", VOLUME))[0]
    return {
        "id": inspected["Id"],
        "running": inspected["State"]["Running"],
        "started_at": inspected["State"]["StartedAt"],
        "volume": (volume["Name"], volume["CreatedAt"]),
        "row": sql("SELECT v FROM seed WHERE k = 'row'"),
    }


@pytest.mark.proves("WR-ENV-10", "WR-ENV-10:dispositions-complete", "B", "B", "DOCKER+PROC", "HOST")
def test_one_call_reused_started_repaired_found_untouched_no_volume_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, prestarted: None
) -> None:
    docker_path = shutil.which("docker")
    assert docker_path is not None
    environ, log = healthy_case.app_environ(tmp_path)
    for name, value in environ.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv(_bind.DOCKER_PATH_ENV, docker_path)
    monkeypatch.delenv(_bind.PORTS_ENV, raising=False)
    monkeypatch.setenv("PYTHONPATH", harness.plugin_pythonpath())
    before = snapshot()
    kernel = healthy_case.kernel(tmp_path)
    done, pid, port = healthy_case.run_and_kill(kernel, "healthy-host", log)
    healthy_case.assert_repaired(kernel, done, pid, log, port, FOUND)
    left = docker("ps", "-a", "--format", "{{.Names}}", "--filter", f"name=trwr-{done.run_id}-")
    assert left == "", left  # the created container: observed absent
    assert snapshot() == before  # the found one and its volume: exactly as they were
