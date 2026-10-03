"""HOST: probe first, submit once, verified by the real database; a second equivalent run reuses
the record with no submit; the system test runs after the provisioning postcondition (L.RB-6.3;
B3.1, B3.2, WR-ENV-5; DOCKER · HOST, run only by the host-docker gate).

The published reference plugin runs on the real binding over the operator's docker (the pinned
images) with the stub toolchain (`twin.toolchain_seam.real_docker_stub_toolchain_ports`; the real
mise is unverified, OPEN-MISE-HOST) and the catalog of `twin.provisioning_case` (one test marked
`provision`). The environment's own Postgres is the pre-started fixture container `postgres`
(`fixtures/reuse-postgres`): `postgres` reuses it (L.RB-3.1) and the operator names it as
the record store (`TRESTLE_ENV_RECORD_STORE`, L.RB-6.2.fix1), so the durable record outlives a run
and a second equivalent run can find it. The record is counted with an authenticated `SELECT` over
the container's own non-loopback address (as L.RB-6.1's probe), never a socket or loopback.
Twins: `twin/test_provisioning_twin.py` (same node names).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from tests.proof import tolerances
from trestle_packs.provision.postgres_record import TABLE
from twin import harness, provisioning_case
from twin.toolchain_seam import __file__ as SEAM_FILE

from trestle_env import tree
from trestle_env.plugins import _bind

pytestmark = pytest.mark.docker_host

CASE = provisioning_case
COMPOSE = Path(__file__).resolve().parents[1] / "fixtures" / "reuse-postgres" / "compose.yaml"
PROJECT = "reuse-postgres"
STORE = tree.POSTGRES_SERVICE  # the fixture container's name


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
    """An authenticated query over TCP to the container's own address (never loopback)."""
    return docker(
        "exec", "-e", f"PGPASSWORD={tree.POSTGRES_FIXTURE_PASSWORD}", STORE, "sh", "-c",
        f'exec psql -X -h "$(hostname -i | cut -d" " -f1)" -U {tree.POSTGRES_USER} '
        f'-d {tree.POSTGRES_DATABASE} -tAc "{statement}"',
    )  # fmt: skip


def records() -> int:
    return int(sql(f"SELECT count(*) FROM {TABLE} WHERE system = '{tree.POSTGRES_SERVICE}'"))


@pytest.fixture
def environment() -> Iterator[None]:
    """The environment's own Postgres: started and answering; removed with its volume after."""
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
        yield
    finally:
        docker("compose", "-p", PROJECT, "-f", str(COMPOSE), "down", "-v", check=False)


def host_environ(tmp_path: Path) -> dict[str, str | None]:
    docker_path = shutil.which("docker")
    assert docker_path is not None
    world = CASE.world(tmp_path / "world-dir")
    return {
        **harness.host_environ(f"{SEAM_FILE}:real_docker_stub_toolchain_ports"),
        **world.toolchain_environ(),
        _bind.DOCKER_PATH_ENV: docker_path,
        _bind.RECORD_STORE_ENV: STORE,
    }


@pytest.mark.proves("WR-ENV-5", "WR-ENV-5:after-provisioning", "B", "B", "DOCKER", "HOST")
@pytest.mark.proves("WR-ENV-4", "B3.1", "B", "B", "DOCKER", "HOST")
def test_probe_first_submit_once_verified_by_db(tmp_path: Path, environment: None) -> None:
    with harness.reference_host(tmp_path / "home", host_environ(tmp_path)) as host:
        _, entries, where = CASE.run(host, "provision-host")
    assert CASE.submits(entries) == 1
    CASE.assert_probe_first(where, entries)
    CASE.assert_never_released(entries)
    assert records() == 1  # exactly one fixture row, read from the real database


@pytest.mark.proves("WR-ENV-5", "WR-ENV-5:after-provisioning", "B", "B", "DOCKER", "HOST")
@pytest.mark.proves("WR-ENV-4", "B3.2", "B", "B", "DOCKER", "HOST")
def test_second_equivalent_run_reuses_no_submit(tmp_path: Path, environment: None) -> None:
    with harness.reference_host(tmp_path / "home", host_environ(tmp_path)) as host:
        _, first, _ = CASE.run(host, "provision-host")
        _, second, _ = CASE.run(host, "provision-host")
    assert (CASE.submits(first), CASE.submits(second)) == (1, 0)
    CASE.assert_never_released(first + second)
    assert records() == 1


@pytest.mark.proves("WR-ENV-5", "WR-ENV-5:after-provisioning", "B", "B", "DOCKER", "HOST")
def test_system_test_after_provisioning_postcondition(tmp_path: Path, environment: None) -> None:
    with harness.reference_host(tmp_path / "home", host_environ(tmp_path)) as host:
        _, entries, _ = CASE.run(host, "provision-host")
    CASE.assert_test_after_postcondition(entries)
    assert records() == 1
