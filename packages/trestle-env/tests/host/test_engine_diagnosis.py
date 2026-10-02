"""HOST: the real Docker CLI at an unreachable endpoint is DOCKER_ENGINE_UNREACHABLE through the
workflow, distinct from DOCKER_CLI_MISSING, and the host engine is unchanged (L.RB-7.1; B4.2,
WR-ENV-6; DOCKER · HOST, run only by the host-docker gate).

One `run(reference_env, completion="terminal")` with the reference plugin's real binding (MC-B-01
`bind`: the operator's absolute docker path) given the endpoint `unix://<tmp>/absent.sock` through
`TRESTLE_DOCKER_ENDPOINT`, the variable the composition root reads. The node itself checks the host
engine around that run at the GATE's endpoint: `<docker> --host <gate endpoint> version --format
'{{.Server.Version}}'` exits 0 before and after with the same non-empty server version, so
`WR-ENV-6:host-engine-unchanged` rests on what this node observes, never on its own run's record
(the record's `engine{reachable_before, reachable_after, server_version}` stays the MJ acceptance).
The twin is `twin/test_engine_diagnosis_twin.py`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from tests.proof import tolerances
from twin import engine_diagnosis, harness

from trestle_env.plugins import _bind

pytestmark = pytest.mark.docker_host


def server_version(cli: str, endpoint: str) -> str:
    done = subprocess.run(  # noqa: S603
        [cli, "--host", endpoint, "version", "--format", "{{.Server.Version}}"],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=tolerances.JOIN_WAIT_S,
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip(), "a non-empty server version"
    return done.stdout.strip()


@pytest.mark.proves("WR-ENV-6", "WR-ENV-6:host-engine-unchanged", "B", "B", "DOCKER", "HOST")
@pytest.mark.proves("WR-ENV-6", "B4.2", "B", "B", "DOCKER", "HOST")
def test_unreachable_endpoint_diagnosed_distinct_from_cli_missing(tmp_path: Path) -> None:
    cli = shutil.which("docker")  # the operator's absolute path (MC-B-01 binds it)
    assert cli is not None, "the host-docker gate runs with a docker CLI (preflight)"
    gate_endpoint = os.environ[_bind.ENDPOINT_ENV]
    before = server_version(cli, gate_endpoint)
    absent = f"unix://{tmp_path}/absent.sock"
    environ = {
        **harness.host_environ(),
        _bind.DOCKER_PATH_ENV: cli,
        _bind.ENDPOINT_ENV: absent,
    }
    with harness.reference_host(tmp_path / "home", environ) as host:
        answer = harness.run_terminal(host, env="engine-unreachable-host")
    engine_diagnosis.assert_diagnosed_unreachable(answer)
    assert os.environ[_bind.ENDPOINT_ENV] == gate_endpoint  # restored for the gate's own checks
    assert server_version(cli, gate_endpoint) == before  # the host engine is unchanged
