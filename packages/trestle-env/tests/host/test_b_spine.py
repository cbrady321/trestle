"""L.RB-0.4: the B-spine on a healthy machine (DOCKER · HOST; run only by the host-docker gate).

One `run(reference_env, completion="terminal")` through the MC-12 MCP host, bound to the operator's
docker at the gate's endpoint (`TRESTLE_DOCKER_ENDPOINT`) with the digest-pinned Postgres image
(`TRESTLE_IMAGE_POSTGRES`), both exported by `docker_gate run`. Facts are read from outside: the
engine inventory before and after (the gate's own `inventory.snapshot`), the run's lane (the proof
court's oracle) and the wire answer. Each node's CI twin is `twin/test_b_spine_twin.py` (same node
name, the fake binding).

* passed: MC-12 counts one request; the answer is `passed`; the dispositions equal what the
  snapshot before the call implies (no Postgres was there, so `backend.postgres` was `started`);
  the create's claim precedes its confirmation (MC-10); RunView.cleanup reports the container
  released and no container with the run's selector prefix exists afterwards (released means
  observed absent, which implies stopped, V-10.4).
* wrong password: the real binding with a planted wrong `PGPASSWORD` in the readiness exec
  environment. The authenticated `SELECT 1` over TCP to the container's own non-loopback address
  never passes (the image trusts loopback and sockets without a password, KDD 2), so the answer is
  not `passed`, `backend.postgres` is never `started`, and the recorded check argv never names a
  loopback address or a socket.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

import pytest
from tests.proof.host.docker_gate import inventory
from twin import fake_binding, harness

from trestle_env import tree

pytestmark = pytest.mark.docker_host

POSTGRES = tree.POSTGRES_UNIT


def _engine() -> tuple[str, str | None]:
    cli = shutil.which("docker")  # the operator's docker; the adapter itself never searches
    assert cli is not None, "the host-docker gate runs with a docker CLI (preflight)"
    return cli, os.environ.get("TRESTLE_DOCKER_ENDPOINT")


def _containers(cli: str, endpoint: str | None) -> list[str]:
    snap = inventory.snapshot(cli, endpoint)
    assert snap["engine"]["reachable"], snap["engine"]
    return [name for obj in snap["containers"] for name in inventory.object_names(obj)]


def _expected(before: list[str]) -> dict[str, Any]:
    """What the snapshot implies: a service found running under its logical name would be
    reused; none there means the run starts one (the supporting service and Postgres alike)."""
    return {
        unit: "reused" if service in before else "started"
        for unit, service in (
            (tree.HTTP_SUPPORT_UNIT, tree.HTTP_SUPPORT_SERVICE),
            (POSTGRES, tree.POSTGRES_SERVICE),
        )
    }


@pytest.mark.proves(
    "WR-ENV-10", "WR-ENV-10:one-request-dispositions-match-snapshot", "B", "B", "DOCKER", "HOST"
)
@pytest.mark.proves("WR-ENV-10", "B1.1", "B", "B", "DOCKER", "HOST")
@pytest.mark.proves("WR-ENV-10", "B8.2", "B", "B", "DOCKER", "HOST")
def test_one_call_passed_healthy_machine(tmp_path: Path) -> None:
    cli, endpoint = _engine()
    before = _containers(cli, endpoint)
    with harness.reference_host(tmp_path / "home", harness.host_environ()) as host:
        answer = harness.run_terminal(host, env="b-spine-host")
        assert answer["state"] == "succeeded", answer
        assert answer["outcome"]["class"] == "passed", answer
        assert answer["answer"]["outcome"] == "passed", answer
        run_id = answer["run_id"]
        entries = harness.lane(harness.run_dir(host, run_id))
    assert harness.dispositions(answer) == _expected(before)
    assert harness.claim_precedes_create(entries)
    prefix = harness.selector_prefix(run_id)
    confirmed = [e for e in entries if e["class"] == "confirmation" and e["effect"] == tree.UP]
    assert {e["path"] for e in confirmed} == {tree.HTTP_SUPPORT_UNIT, POSTGRES}
    for entry in confirmed:
        assert str(entry["identity"]).startswith(prefix), entry  # this run created each
    assert harness.container_released(answer)
    assert not [n for n in _containers(cli, endpoint) if n.startswith(prefix)]  # absent after


@pytest.mark.proves(
    "WR-ENV-10", "WR-ENV-10:readiness-authenticated-postgres", "B", "B", "DOCKER", "HOST"
)
def test_wrong_postgres_password_never_ready(tmp_path: Path) -> None:
    cli, endpoint = _engine()
    log = tmp_path / "argv.jsonl"
    environ = {
        **harness.host_environ(fake_binding.REAL_WRONG_PASSWORD_SEAM),
        fake_binding.ARGV_LOG_ENV: str(log),
    }
    with harness.reference_host(tmp_path / "home", environ) as host:
        answer = harness.run_terminal(host, env="b-spine-host-wrong")
        run_id = answer["run_id"]
    assert answer["answer"]["outcome"] != "passed", answer  # the tree's class
    assert harness.dispositions(answer).get(POSTGRES) != "started"
    execs = [argv for argv in fake_binding.read_argv_log(log) if "exec" in argv]
    assert execs, "the readiness check ran"
    for argv in execs:
        command = " ".join(argv)
        assert f"PGPASSWORD={fake_binding.WRONG_PASSWORD}" in argv  # the planted password
        assert "hostname -i" in command and "-h " in command  # the container's own address
        assert "127.0.0.1" not in command and "localhost" not in command and "::1" not in command
        assert "pg_isready" not in command
    assert not [
        n for n in _containers(cli, endpoint) if n.startswith(harness.selector_prefix(run_id))
    ]
