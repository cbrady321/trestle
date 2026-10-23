"""Optional live Docker integration test — skipped when docker daemon unavailable.

`docker_stack` (the docker pack) drives the operator's real `docker compose` through
`FakeContext.exec`: the stack comes up, its log is attached, and `action: down` takes it down."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from pack_wrappers import load_wrapper
from trestle.plugin.testing import FakeContext, call_plugin

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "minimal-compose"
COMPOSE_IMAGE = "alpine:3.20"


def _docker_daemon_ready() -> bool:
    if shutil.which("docker") is None:
        return False
    proc = subprocess.run(["docker", "info"], capture_output=True, check=False)
    return proc.returncode == 0


def _compose_image_local() -> bool:
    proc = subprocess.run(
        ["docker", "image", "inspect", COMPOSE_IMAGE],
        capture_output=True,
        check=False,
    )
    return proc.returncode == 0


def _live_compose_ready() -> bool:
    return _docker_daemon_ready() and _compose_image_local()


@pytest.mark.skipif(
    not _live_compose_ready(),
    reason=f"docker compose live test needs daemon + local image {COMPOSE_IMAGE}",
)
def test_stack_runner_live_compose(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    compose = FIXTURE_DIR / "docker-compose.yml"
    workdir = tmp_path / "project"
    workdir.mkdir()
    compose_text = compose.read_text(encoding="utf-8")
    (workdir / "docker-compose.yml").write_text(compose_text, encoding="utf-8")
    monkeypatch.delenv("TRESTLE_DOCKER_PATH", raising=False)

    spec = {
        "compose_file": "docker-compose.yml",
        "project": "trestle-packs-smoke",
        "teardown": "down",
        "waves": [{"name": "app", "services": ["web"], "wait": "started", "timeout_s": 60}],
    }
    plugin = load_wrapper("docker_stack").docker_stack
    ctx = FakeContext(tmp_path / "ctx")
    try:
        result = call_plugin(plugin, {"spec": spec, "workdir": str(workdir)}, ctx=ctx)
        assert isinstance(result, dict) and result["ok"] is True, result
        assert result["waves_completed"] == ["app"]
        assert "web.log" in ctx.artifacts
    finally:
        down = call_plugin(
            plugin, {"spec": spec, "workdir": str(workdir), "action": "down"}, ctx=ctx
        )
    assert isinstance(down, dict) and down["ok"] is True, down
    left = subprocess.run(
        ["docker", "ps", "-aq", "--filter", "label=com.docker.compose.project=trestle-packs-smoke"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert left.stdout.strip() == ""
