"""Integration pipeline — docker stack, migrate, pytest in one run."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from trestle_packs.core.paths import resolve_under
from trestle_packs.docker.runner import StackRunner
from trestle_packs.docker.spec import StackSpec
from trestle_packs.migrate.alembic_runner import run_alembic_upgrade
from trestle_packs.pytest.runner import PytestSpec, run_pytest

from trestle.plugin.surface import Context, trestle


@dataclass
class PipelineProbe:
    kind: str = "command"
    command: list[str] = field(default_factory=list)


@dataclass
class PipelineWave:
    name: str
    services: list[str]
    wait: str = "healthy"
    timeout_s: float = 120.0


@dataclass
class PipelineResult:
    ok: bool
    stages: dict[str, dict[str, str]]


@dataclass
class PipelineStack:
    project: str | None = None
    compose_file: str = "docker-compose.yml"
    teardown: str = "down"
    waves: list[PipelineWave] = field(default_factory=list)
    probes: dict[str, PipelineProbe] = field(default_factory=dict)
    depends_on: dict[str, list[str]] = field(default_factory=dict)


@trestle
def integration_pipeline(
    ctx: Context,
    stack_spec: PipelineStack,
    alembic_config: str | None = None,
    pytest_path: str = "tests",
    workdir: str | None = None,
) -> PipelineResult:
    """Run docker stack → optional alembic migrate → pytest.

    The stack is stopped exactly once on every path (K-15): `up` tears itself down when it
    fails, and once it has returned the pipeline tears down in `finally` on success and on
    every failure, per the declared `teardown` policy.
    """
    cwd = Path(workdir) if workdir else Path.cwd()
    stages: dict[str, Any] = {}
    stack = StackSpec.from_dict(asdict(stack_spec))
    runner = StackRunner(ctx)

    ctx.log("stage: docker_stack")
    docker_result = runner.up(stack, cwd=cwd)  # a failing `up` runs its own single teardown
    stages["docker"] = docker_result.to_dict()

    try:
        if alembic_config:
            ctx.log("stage: migrate")
            mig = run_alembic_upgrade(resolve_under(cwd, alembic_config))
            stages["migrate"] = mig.to_dict()

        ctx.log("stage: pytest")
        pytest_result = run_pytest(
            PytestSpec(path=str(resolve_under(cwd, pytest_path))),
            report_dir=ctx.outputs,
        )
        if pytest_result.report_path:
            ctx.attach(Path(pytest_result.report_path), name="pytest-report.json")
        stages["pytest"] = pytest_result.to_dict()

        if pytest_result.exit_code != 0:
            msg = f"pytest failed with exit code {pytest_result.exit_code}"
            raise RuntimeError(msg)

        return {"stages": stages, "ok": True}
    finally:
        runner.down(stack, cwd=cwd)
