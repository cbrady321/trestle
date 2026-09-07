"""Integration tests for StackRunner wave sequencing and teardown."""

from __future__ import annotations

from pathlib import Path

import pytest
from fake_compose_backend import FakeComposeBackend
from fake_pack_context import FakePackContext

from trestle_packs.docker.runner import StackRunner
from trestle_packs.docker.spec import StackSpec, WaitMode


def _stack_spec() -> StackSpec:
    return StackSpec.from_dict(
        {
            "compose_file": "docker-compose.yml",
            "project": "test-stack",
            "teardown": "down",
            "waves": [
                {
                    "name": "data",
                    "services": ["postgres", "redis"],
                    "wait": "healthy",
                    "timeout_s": 30,
                },
                {
                    "name": "app",
                    "services": ["api"],
                    "wait": "started",
                    "timeout_s": 60,
                },
            ],
            "depends_on": {"api": ["postgres", "redis"]},
        }
    )


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    compose = tmp_path / "docker-compose.yml"
    compose.write_text("services: {}\n", encoding="utf-8")
    return tmp_path


def test_stack_runner_wave_sequencing(workdir: Path) -> None:
    backend = FakeComposeBackend()
    ctx = FakePackContext(work=workdir)
    runner = StackRunner(ctx, backend=backend)

    result = runner.up(_stack_spec(), cwd=workdir)

    assert result.waves_completed == ["data", "app"]
    assert [call.services for call in backend.up_calls] == [
        ["postgres", "redis"],
        ["api"],
    ]
    assert backend.up_calls[0].wait == WaitMode.HEALTHY
    assert backend.up_calls[1].wait == WaitMode.STARTED
    assert backend.down_calls == []
    assert set(ctx.attached) == {"postgres.log", "redis.log", "api.log"}
    assert "postgres started" in ctx.attached["postgres.log"].read_text(encoding="utf-8")


def test_stack_runner_teardown_stop_policy(workdir: Path) -> None:
    backend = FakeComposeBackend(fail_on_wave=1)
    ctx = FakePackContext(work=workdir)
    runner = StackRunner(ctx, backend=backend)
    spec = StackSpec.from_dict(
        {
            "compose_file": "docker-compose.yml",
            "teardown": "stop",
            "waves": [
                {
                    "name": "data",
                    "services": ["postgres", "redis"],
                    "wait": "healthy",
                    "timeout_s": 30,
                },
                {
                    "name": "app",
                    "services": ["api"],
                    "wait": "started",
                    "timeout_s": 60,
                },
            ],
            "depends_on": {"api": ["postgres", "redis"]},
        }
    )

    with pytest.raises(RuntimeError, match="simulated failure on wave 1"):
        runner.up(spec, cwd=workdir)

    assert backend.stop_calls == 1
    assert backend.down_calls == []
    assert "teardown: compose stop complete" in ctx.logs


def test_stack_runner_teardown_on_failure(workdir: Path) -> None:
    backend = FakeComposeBackend(fail_on_wave=1)
    ctx = FakePackContext(work=workdir)
    runner = StackRunner(ctx, backend=backend)

    with pytest.raises(RuntimeError, match="simulated failure on wave 1"):
        runner.up(_stack_spec(), cwd=workdir)

    assert [call.services for call in backend.up_calls] == [["postgres", "redis"]]
    assert backend.down_calls == [True]
    assert "teardown: compose down complete" in ctx.logs
