"""HOST: no registered teardown deletes a seeded named volume on real Docker (L.RB-3.3; K-6,
WR-OWN-4, the F-13(d) wider-reading fixture; DOCKER · HOST, run only by the host-docker gate).

Every case of `twin.k6.CASES` is one call through `run` on the MC-12 MCP host: the legacy
`docker_stack` and `integration_pipeline` plugins (examples/packs, published unedited, over their
own `WhaleComposeBackend` and the operator's docker) under each teardown value, and the reference
plugin's release on its real binding. After the answer the named volume is still there and still
holds its seed, read with plain docker calls at the gate's endpoint (never through the code under
test). The legacy stacks use the fixture's own Compose project, so what `stop`, `none` or a failed
`up` leaves is fixture-labelled residue (CSC-10); each case removes its own objects afterwards.
The twin is `twin/test_k6_entry_points_twin.py` (same parametrization).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest
from tests.proof import tolerances
from twin import harness, k6

pytestmark = pytest.mark.docker_host


class Docker:
    """Plain docker calls at the gate's endpoint, bounded, stdin closed."""

    def __init__(self) -> None:
        cli = shutil.which("docker")  # the operator's docker (the preflight resolved it)
        assert cli is not None, "the host-docker gate runs with a docker CLI (preflight)"
        self.argv = [cli, "--host", os.environ["TRESTLE_DOCKER_ENDPOINT"]]
        self.image = os.environ["TRESTLE_IMAGE_ALPINE"]  # `<repo>@sha256:<hex>`, never pulled

    def __call__(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        env = {**os.environ, k6.BROKEN_IMAGE_ENV: k6.MISSING_IMAGE}
        return subprocess.run(  # noqa: S603
            [*self.argv, *args],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            env=env,
            check=check,
            timeout=tolerances.JOIN_WAIT_S * 6,
        )

    def seed_of(self, volume: str) -> str:
        """The seed the volume holds, read by a throwaway labelled container."""
        done = self(
            "run", "--rm", "--pull", "never", "--label", f"{k6.FIXTURE_LABEL}={k6.FIXTURE}",
            "-v", f"{volume}:/data", self.image, "cat", k6.SEED_FILE,
        )  # fmt: skip
        return done.stdout.strip()

    def volume_present(self, volume: str) -> bool:
        return self("volume", "inspect", volume, check=False).returncode == 0


@pytest.mark.proves("WR-OWN-4", "WR-OWN-4:registered-entry-points-host", "B", "B", "DOCKER", "HOST")
@pytest.mark.parametrize("case", k6.CASES)
def test_no_volume_deleted_by_any_registered_teardown(case: str, tmp_path: Path) -> None:
    docker = Docker()
    plugin, teardown = k6.split(case)
    if plugin == "reference_env":
        volume = f"trestle-k6-ref-{uuid.uuid4().hex[:8]}"
        docker("volume", "create", "--label", f"{k6.FIXTURE_LABEL}={k6.FIXTURE}", volume)
        try:
            docker(
                "run", "--rm", "--pull", "never", "--label", f"{k6.FIXTURE_LABEL}={k6.FIXTURE}",
                "-v", f"{volume}:/data", docker.image, "sh", "-c", f"echo seeded > {k6.SEED_FILE}",
            )  # fmt: skip
            with harness.reference_host(tmp_path / "home", harness.host_environ()) as host:
                answer = harness.run_terminal(host, env=f"k6-release-{uuid.uuid4().hex[:6]}")
            assert answer["answer"]["outcome"] == "passed", answer
            assert harness.container_released(answer)
            assert docker.volume_present(volume)  # the release removed no volume
            assert docker.seed_of(volume) == "seeded"
        finally:
            docker("volume", "rm", "-f", volume, check=False)
        return
    project = k6.project_name(case)
    work = k6.workdir(tmp_path, case)
    environ = {"DOCKER_HOST": os.environ["TRESTLE_DOCKER_ENDPOINT"]}
    try:
        with k6.legacy_host(tmp_path / "home", plugin, twin=False, environ=environ) as host:
            answer = k6.call(host, plugin, k6.legacy_args(plugin, teardown, project, work))
        k6.expect_state(plugin, answer)
        volume = k6.volume_name(project)
        assert docker.volume_present(volume), f"{case}: the seeded volume is gone"
        assert docker.seed_of(volume) == "seeded"
    finally:
        compose = ["compose", "-p", project, "-f", str(work / "compose.yaml")]
        docker(*compose, "down", "--volumes", "--timeout", "1", check=False)
