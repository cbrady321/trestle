"""HOST: the documented teardown removal sets equal what real Docker removes (L.RB-3.4; K-6).

docs/packs.md's `### Teardown` table (landed at NW-1, read only here) is parsed and each row is run
on a fresh one-service stack through the legacy `StackRunner` over its own python-on-whales backend
and the operator's docker at the gate's endpoint (`TRESTLE_DOCKER_ENDPOINT`, image
`TRESTLE_IMAGE_ALPINE`, both exported by `docker_gate run`); the objects are read with plain docker
calls, never through the backend under test. Every object carries the fixture label, and each case
removes its own stack afterwards (`docker_gate run` housekeeping would too, CSC-10)."""

from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest
from tests.proof import tolerances
from trestle_packs.docker.compose_whale import WhaleComposeBackend
from twin import k6_docs


class RealEngine:
    def __init__(self, cli: str, endpoint: str) -> None:
        self.cli = cli
        self.endpoint = endpoint

    def backend(self) -> WhaleComposeBackend:
        return WhaleComposeBackend()

    def _ids(self, *args: str) -> frozenset[str]:
        done = subprocess.run(  # noqa: S603
            [self.cli, "--host", self.endpoint, *args],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            check=True,
            timeout=tolerances.JOIN_WAIT_S,
        )
        return frozenset(done.stdout.split())

    def objects(self, project: str) -> dict[str, frozenset[str]]:
        label = f"label=com.docker.compose.project={project}"
        return {
            "containers": self._ids("ps", "-a", "-q", "--no-trunc", "--filter", label),
            "networks": self._ids("network", "ls", "-q", "--no-trunc", "--filter", label),
            "volumes": self._ids("volume", "ls", "-q", "--filter", label),
        }

    def remove(self, workdir: Path, project: str) -> None:
        subprocess.run(  # noqa: S603
            [
                self.cli,
                "--host",
                self.endpoint,
                "compose",
                "-p",
                project,
                "-f",
                str(workdir / "compose.yaml"),
                "down",
                "--volumes",
                "--timeout",
                "1",
            ],
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=tolerances.JOIN_WAIT_S * 3,
        )


@pytest.mark.docker_host
@pytest.mark.proves("WR-OWN-4", "WR-OWN-4:docs-equal-observed-host", "B", "B", "DOCKER", "HOST")
def test_documented_removal_sets_equal_observed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cli = shutil.which("docker")  # the test names the operator's path (the preflight resolved it)
    assert cli is not None, "the host-docker gate runs with a docker CLI (preflight)"
    endpoint = os.environ["TRESTLE_DOCKER_ENDPOINT"]
    image = os.environ["TRESTLE_IMAGE_ALPINE"]  # `<repo>@sha256:<hex>`, never pulled
    # the legacy backend reaches the engine the way an operator's shell does: DOCKER_HOST
    monkeypatch.setenv("DOCKER_HOST", endpoint)
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
    engine = RealEngine(cli, endpoint)
    suffix = uuid.uuid4().hex[:8]

    def observe(row: str):
        project = f"trestle-k6docs-{row.replace('_', '-')}-{suffix}"
        workdir = tmp_path / row
        try:
            return k6_docs.observe_case(
                engine, workdir, project, row, image, tolerances.JOIN_WAIT_S * 3
            )
        finally:
            engine.remove(workdir, project)

    k6_docs.assert_docs_equal_observed(observe)
