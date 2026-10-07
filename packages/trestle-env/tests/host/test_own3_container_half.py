"""L.RB-12.6: the WR-OWN-3 container half (K-8's) on real Docker (DOCKER · HOST).

A falsifier over every passed B-suite run of the gate session: after a passed run no container the
run created remains, so none is running (MC-B-01's release stops then removes it, and released
means observed absent, which implies stopped, V-10.4). The node makes two passed runs of its own
(the reference plugin and `tree_variants`), so it is never vacuous, then checks the engine for a
container named with the selector prefix of each passed run the harness saw in this session
(`harness.PASSED_RUNS`, its own included). It adds no product behaviour: what it falsifies is the
loop's release walk on the success path (`trestle/workflow/loop.py`, L.SV-5.8) running the
container port's release. The CI twin is `twin/test_own3_container_half_twin.py`.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from tests.proof.host.docker_gate import inventory
from twin import harness, variants

pytestmark = pytest.mark.docker_host


def _containers() -> list[str]:
    cli = shutil.which("docker")
    assert cli is not None, "the host-docker gate runs with a docker CLI (preflight)"
    snap = inventory.snapshot(cli, os.environ.get("TRESTLE_DOCKER_ENDPOINT"))
    assert snap["engine"]["reachable"], snap["engine"]
    return [n for obj in snap["containers"] for n in inventory.object_names(obj)]


@pytest.mark.proves(
    "WR-OWN-3", "WR-OWN-3:created-container-stopped-on-success", "core", "B", "DOCKER", "HOST"
)
def test_passed_run_leaves_no_created_container(tmp_path: Path) -> None:
    environ = harness.host_environ()
    with harness.reference_host(tmp_path / "ref", environ) as host:
        reference = harness.run_terminal(host, env="own3-host-ref")
        ref_lane = harness.lane(harness.run_dir(host, reference["run_id"]))
    with variants.variants_host(tmp_path / "var", environ) as host:
        tree = variants.run_terminal(host, env="own3-host-var", mode="passed")
    for answer in (reference, tree):
        assert answer["answer"]["outcome"] == "passed", answer
        assert str(answer["run_id"]) in harness.PASSED_RUNS
    assert variants.created(ref_lane), "the run created a container: the check is not vacuous"
    names = _containers()
    for run_id in harness.PASSED_RUNS:
        assert not [n for n in names if n.startswith(harness.selector_prefix(run_id))], run_id
