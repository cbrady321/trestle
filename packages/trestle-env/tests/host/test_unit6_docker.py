"""L.RB-12.2: WR-UNIT-6 on real Docker (DOCKER · HOST; run only by the host-docker gate).

The `tree_variants` plugin with its fake step holding, stopped from outside: `cancel` (the MCP
`cancel` tool once both containers exist) and `deadline` (the step blocks inside one call, and the
root deadline's release point stops the run). After the answer no child-created container exists
(the root released them, WR-UNIT-5); the record holds exactly one stop row and no APPLIED
non-release entry at or past the lane length it recorded (WR-UNIT-6, B2-C15); and a found
container (pre-existing, fixture-labelled, planted before the run) is unchanged: same id, still
running. The CI twin is `twin/test_unit6_docker_twin.py`.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from tests.core.spine import support
from tests.proof import tolerances
from tests.proof.host.docker_gate import inventory
from twin import harness, variants

pytestmark = pytest.mark.docker_host

FIXTURE_LABEL = "trestle.proof.fixture=tree-variants"
KEEP_RUNNING = ("sh", "-c", "trap 'exit 0' TERM; while :; do sleep 1; done")


def _docker(*args: str) -> subprocess.CompletedProcess[str]:
    cli = shutil.which("docker")
    assert cli is not None, "the host-docker gate runs with a docker CLI (preflight)"
    endpoint = os.environ.get("TRESTLE_DOCKER_ENDPOINT")
    return subprocess.run(
        inventory.docker_cmd(cli, endpoint, *args),
        capture_output=True,
        text=True,
        env=inventory.docker_env(),
        stdin=subprocess.DEVNULL,
        timeout=tolerances.JOIN_WAIT_S * 3,
        check=False,
    )


@pytest.fixture
def found() -> Iterator[str]:
    """A found container: running before the run, not the run's, fixture-labelled (CSC-10)."""
    name = f"trestle-found-{uuid.uuid4().hex[:8]}"
    image = os.environ["TRESTLE_IMAGE_ALPINE"]
    made = _docker(
        "run", "-d", "--pull", "never", "--name", name, "--label", FIXTURE_LABEL, image,
        *KEEP_RUNNING,
    )  # fmt: skip
    assert made.returncode == 0, made.stderr
    try:
        yield name
    finally:
        _docker("rm", "-f", name)


def _state(name: str) -> tuple[str, bool]:
    shown = _docker("inspect", "--format", "{{json .}}", name)
    assert shown.returncode == 0, shown.stderr
    data = json.loads(shown.stdout)
    return str(data["Id"]), bool(data["State"]["Running"])


def _run_containers(prefix: str) -> list[str]:
    listed = _docker("ps", "-a", "--format", "{{.Names}}")
    assert listed.returncode == 0, listed.stderr
    return [n for n in listed.stdout.split() if n.startswith(prefix)]


CASES = [
    pytest.param(
        "cancel",
        marks=[
            pytest.mark.proves("WR-UNIT-5", "WR-UNIT-5:b-root-cancel", "B", "B", "DOCKER", "HOST"),
            pytest.mark.proves(
                "WR-UNIT-6", "WR-UNIT-6:b-no-action-after-stop", "B", "B", "DOCKER", "HOST"
            ),
            pytest.mark.proves("WR-UNIT-6", "B6.3", "B", "B", "DOCKER", "HOST"),
        ],
        id="cancel",
    ),
    pytest.param(
        "deadline",
        marks=[
            pytest.mark.proves(
                "WR-UNIT-5", "WR-UNIT-5:b-root-deadline", "B", "B", "DOCKER", "HOST"
            ),
            pytest.mark.proves(
                "WR-UNIT-6", "WR-UNIT-6:b-no-action-after-stop", "B", "B", "DOCKER", "HOST"
            ),
            pytest.mark.proves("WR-UNIT-6", "B6.3", "B", "B", "DOCKER", "HOST"),
        ],
        id="deadline",
    ),
]


@pytest.mark.parametrize("mode", CASES)
def test_after_root_stop_no_child_container_remains(mode: str, found: str, tmp_path: Path) -> None:
    before = _state(found)
    with variants.variants_host(tmp_path / "home", harness.host_environ()) as host:
        if mode == "cancel":
            run_id = variants.start(host, env="unit6-host-cancel", mode=mode)
            assert support.wait_until(
                lambda: variants.both_created(host, run_id), tolerances.JOIN_WAIT_S * 6
            ), "both containers were never created"
            view = variants.cancel_and_await(host, run_id)
        else:
            view = variants.run_terminal(host, env="unit6-host-deadline", mode=mode)
            run_id = view["run_id"]
        run_dir = harness.run_dir(host, run_id)
    expected = {"cancel": "cancelled", "deadline": "timed_out"}[mode]
    assert view["answer"]["outcome"] == expected, view
    assert variants.applied_past_stop(run_dir) == []
    assert _run_containers(harness.selector_prefix(run_id)) == []
    assert _state(found) == before == (before[0], True)  # the found container is untouched
