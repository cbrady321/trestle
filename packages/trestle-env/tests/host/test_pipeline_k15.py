"""L.RB-11.1: K-15 on real Docker (DOCKER · HOST; run only by the host-docker gate).

The legacy `integration_pipeline` (examples/packs, NW-1's output, unedited), published and driven
through `run` on the MC-12 MCP host, stops its Compose stack exactly once on success, on a
failing pytest stage and on a failed `up`: after the answer no container of the run's Compose
project remains, and the run's events hold exactly one "teardown: compose down complete". The
stack is the K-15 fixture (`fixtures/k15-compose`, image by role, fixture-labelled); the `up`
failure names an uncached image digest, which `pull_policy: never` refuses to pull. The CI twin is
`twin/test_pipeline_k15_twin.py` (same parametrization, recording in-memory backend).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from tests.proof.host.docker_gate import inventory
from twin import k15

pytestmark = pytest.mark.docker_host


def _project_containers(project: str) -> list[str]:
    cli = shutil.which("docker")
    assert cli is not None, "the host-docker gate runs with a docker CLI (preflight)"
    snap = inventory.snapshot(cli, os.environ.get("TRESTLE_DOCKER_ENDPOINT"))
    assert snap["engine"]["reachable"], snap["engine"]
    return [
        name
        for obj in snap["containers"]
        for name in inventory.object_names(obj)
        if name.startswith(f"{project}-")
    ]


@pytest.mark.proves("WR-ENV-11", "WR-ENV-11:pipeline-one-teardown-host", "B", "B", "DOCKER", "HOST")
@pytest.mark.proves("WR-ENV-11", "WR-ENV-11:entry-points-callable-host", "B", "B", "DOCKER", "HOST")
@pytest.mark.parametrize("variant", k15.VARIANTS)
def test_pipeline_stops_stack_once(variant: str, tmp_path: Path) -> None:
    environ = {"TRESTLE_IMAGE_ALPINE": k15.MISSING_IMAGE} if variant == "up_failure" else {}
    project = k15.project_name(variant)
    work = k15.workdir(tmp_path, variant)
    with k15.pipeline_host(tmp_path / "home", twin=False, environ=environ) as host:
        answer = k15.run_pipeline(host, work, project)
        k15.expect_state(variant, answer)
        assert k15.teardown_events(host, answer["run_id"]) == 1
    assert _project_containers(project) == []
