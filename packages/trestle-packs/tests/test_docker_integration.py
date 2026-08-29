"""Optional live Docker integration test — skipped when docker daemon unavailable."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from fake_pack_context import FakePackContext

from trestle_packs.docker.compose_whale import WhaleComposeBackend
from trestle_packs.docker.runner import StackRunner
from trestle_packs.docker.spec import StackSpec

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "minimal-compose"


def _docker_daemon_ready() -> bool:
    if shutil.which("docker") is None:
        return False
    proc = subprocess.run(["docker", "info"], capture_output=True, check=False)
    return proc.returncode == 0


@pytest.mark.skipif(not _docker_daemon_ready(), reason="docker daemon not available")
def test_stack_runner_live_compose(tmp_path: Path) -> None:
    compose = FIXTURE_DIR / "docker-compose.yml"
    workdir = tmp_path / "project"
    workdir.mkdir()
    compose_text = compose.read_text(encoding="utf-8")
    (workdir / "docker-compose.yml").write_text(compose_text, encoding="utf-8")

    spec = StackSpec.from_dict(
        {
            "compose_file": "docker-compose.yml",
            "project": "trestle-packs-smoke",
            "teardown": "down",
            "waves": [{"name": "app", "services": ["web"], "wait": "started", "timeout_s": 60}],
        }
    )
    ctx = FakePackContext(work=workdir)
    backend = WhaleComposeBackend()
    runner = StackRunner(ctx, backend=backend)

    try:
        result = runner.up(spec, cwd=workdir)
        assert result.waves_completed == ["app"]
        assert "web.log" in ctx.attached
    finally:
        runner.down(spec, cwd=workdir)
