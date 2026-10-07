"""L.RB-12.1: WR-UNIT-5 on real Docker (DOCKER · HOST; run only by the host-docker gate).

The `tree_variants` test plugin (`fixtures/tree_variants.py`) runs the reference Postgres child and
a second created container (`backend.helper`, which needs Postgres) beside a fake step, through
one MCP `run` per variant, on the operator's docker. On a passed run, a failing sibling and the
container child's own exception alike: each create's claim precedes its confirmation (MC-10); both
containers are released at the root's release phase (every owned stop after the root's `end`), the
dependent before its dependency (reverse dependency order), through the container port's stop and
remove; RunView.cleanup reports them released; and no container with the run's selector prefix
exists after the answer (released means observed absent, V-10.4). The CI twin is
`twin/test_unit5_docker_twin.py` (same parametrization, the fake binding).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from tests.proof.host.docker_gate import inventory
from twin import harness, variants

pytestmark = pytest.mark.docker_host


def _run_containers(prefix: str) -> list[str]:
    cli = shutil.which("docker")
    assert cli is not None, "the host-docker gate runs with a docker CLI (preflight)"
    snap = inventory.snapshot(cli, os.environ.get("TRESTLE_DOCKER_ENDPOINT"))
    assert snap["engine"]["reachable"], snap["engine"]
    names = (n for obj in snap["containers"] for n in inventory.object_names(obj))
    return [n for n in names if n.startswith(prefix)]


CASES = [
    pytest.param(
        "passed",
        marks=[
            pytest.mark.proves(
                "WR-UNIT-5", "WR-UNIT-5:b-passed-released", "B", "B", "DOCKER", "HOST"
            ),
            pytest.mark.proves("WR-UNIT-5", "B8.5", "B", "B", "DOCKER", "HOST"),
        ],
        id="passed",
    ),
    pytest.param(
        "sibling_failure",
        marks=pytest.mark.proves(
            "WR-UNIT-5", "WR-UNIT-5:b-sibling-failure", "B", "B", "DOCKER", "HOST"
        ),
        id="sibling_failure",
    ),
    pytest.param(
        "child_exception",
        marks=pytest.mark.proves(
            "WR-UNIT-5", "WR-UNIT-5:b-child-exception", "B", "B", "DOCKER", "HOST"
        ),
        id="child_exception",
    ),
]


@pytest.mark.parametrize("mode", CASES)
def test_child_container_released_by_root(mode: str, tmp_path: Path) -> None:
    with variants.variants_host(tmp_path / "home", harness.host_environ()) as host:
        answer = variants.run_terminal(host, env=f"unit5-host-{mode}", mode=mode)
        run_id = answer["run_id"]
        entries = harness.lane(harness.run_dir(host, run_id))
    assert (answer["answer"]["outcome"] == "passed") == (mode == "passed"), answer
    made = variants.created(entries)
    assert set(made) == {variants.POSTGRES, variants.HELPER}, made
    prefix = harness.selector_prefix(run_id)
    assert all(selector.startswith(prefix) for selector in made.values()), made
    assert variants.claims_precede_creates(entries)
    assert variants.release_order(entries) == [variants.HELPER, variants.POSTGRES]
    assert variants.released_after_root_end(entries)
    assert harness.container_released(answer)
    assert _run_containers(prefix) == []
