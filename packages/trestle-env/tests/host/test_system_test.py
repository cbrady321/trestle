"""The test node starts after every readiness pass, on real Docker (L.RB-5.2; WR-VERIFY-2).

DOCKER + PROC, venue HOST (`docker_host`; `docker_gate run`, F-PX3): the published `reference_env`
on the operator's docker and the pinned images (both backends are real containers), the operator's
catalog listing the test `demo-version`, and the toolchain the mise-shaped STUB (real mise is
unverified, OPEN-MISE-HOST) whose `python` the real task runner runs as a real local process. One
MCP call names the test; the fact is read from the finalized run's lane: the test node's first
entry, its ticket, follows the readiness pass of BOTH containers. CI twin:
`tests/twin/test_system_test_twin.py`."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from twin import harness
from twin.toolchain_seam import __file__ as SEAM_FILE
from twin.toolchain_world import World, call

from trestle_env import tree
from trestle_env.plugins import _bind

pytestmark = pytest.mark.docker_host

TEST_ID = "demo-version"
NODE = f"test.{TEST_ID}"


@pytest.mark.proves(
    "WR-VERIFY-2", "WR-VERIFY-2:b-test-after-readiness", "B", "B", "DOCKER+PROC", "HOST"
)
def test_test_starts_after_every_readiness_pass(tmp_path: Path) -> None:
    docker = shutil.which("docker")  # the operator's path, named here; the adapter never searches
    assert docker is not None, "the host-docker gate runs with a docker CLI (preflight)"
    world = World(tmp_path / "world-dir", installed=True)
    environ = {
        **harness.host_environ(f"{SEAM_FILE}:real_docker_stub_toolchain_ports"),
        **world.toolchain_environ(),
        _bind.DOCKER_PATH_ENV: docker,
    }
    with harness.reference_host(tmp_path / "home", environ) as host:
        answer = call(host, TEST_ID, env="system-test-host")
        entries = harness.lane(harness.run_dir(host, answer["run_id"]))
    assert answer["answer"]["outcome"] == "passed", answer["answer"]
    ended = {e["path"]: n for n, e in enumerate(entries) if e["class"] == "end"}
    first = next(n for n, e in enumerate(entries) if e.get("path") == NODE)
    assert entries[first]["class"] == "issue"
    for backend in (tree.HTTP_SUPPORT_SERVICE, tree.POSTGRES_SERVICE):
        assert ended[backend] < first, backend
