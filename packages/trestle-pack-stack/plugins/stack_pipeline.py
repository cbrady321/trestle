"""stack_pipeline: bring a Compose stack up, apply optional Alembic migrations, run pytest, and
tear the stack down exactly once.

The recipe, top to bottom (adapt it by copying this file and publishing it under a new name):

1. find the project's interpreter and the docker CLI, and check the engine answers (`Blocked`
   when any is missing, before anything starts);
2. docker stage: each wave runs `docker compose ... up --detach [--build] [--no-deps] [--wait]
   <services>` and saves each service's logs; a failed wave runs the declared teardown itself
   (once) and the pipeline raises `Failed("docker_failed")` with `failed_stage: docker`;
3. migrate stage (with `alembic_config`): `<python> -m alembic -c <config> upgrade head`; a
   failed upgrade raises `Failed("migrate_failed")`;
4. pytest stage: `<python> -m pytest <pytest_path> ... --junitxml=<file>`, read from the JUnit
   file; a failing test raises `Failed("pytest_failed")` with `failed_stage: pytest`;
5. teardown, once, on every path after a successful `up`: `docker compose ... down` (volumes
   kept), `stop` or nothing, as `stack_spec.teardown` declares.

A failed stage's `Failed` carries the whole result (`ok: false`, `failed_stage`, each stage's
answer and the teardown), so the run answers `failed` and its summary still shows the stages.

The docker commands are the same as `docker_stack`'s; every command goes through `ctx.exec`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from trestle_pack_docker import CycleError, locate_docker, plan_waves, resolve_under, tail
from trestle_pack_docker.results import StackResult  # where they are defined: the schema
from trestle_pack_migrate import MigrateResult  # reads the result types from there
from trestle_pack_pytest import find_project_python, read_junit_report
from trestle_pack_pytest.junit import PytestReport

from trestle.plugin import Blocked, Context, Failed, trestle


@dataclass
class PipelineProbe:
    kind: Literal["command"] = "command"
    command: list[str] = field(default_factory=list)


@dataclass
class PipelineWave:
    name: str
    services: list[str]
    wait: Literal["healthy", "started"] = "healthy"
    timeout_s: float = 120.0


@dataclass
class PipelineStack:
    """The stack, as `docker_stack`'s spec (custom probe waits are `docker_stack`'s only)."""

    project: str | None = None
    compose_file: str = "docker-compose.yml"
    compose_files: list[str] = field(default_factory=list)
    profiles: list[str] = field(default_factory=list)
    env_files: list[str] = field(default_factory=list)
    build: bool = False
    no_deps: bool = False
    teardown: Literal["down", "stop", "none"] = "down"
    waves: list[PipelineWave] = field(default_factory=list)
    probes: dict[str, PipelineProbe] = field(default_factory=dict)
    depends_on: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class PipelineResult:
    """`ok` is the verdict; `failed_stage` names the first stage that failed (`docker`,
    `migrate`, `pytest`); each stage that ran has its own answer (`docker`, `migrate`, `pytest`).
    `teardown` is what stopped the stack (`down`, `stop`, `none`)."""

    ok: bool
    failed_stage: str | None = None
    teardown: str | None = None
    docker: StackResult | None = None
    migrate: MigrateResult | None = None
    pytest: PytestReport | None = None


def compose_command(docker: str, stack: PipelineStack, workdir: Path) -> list[str]:
    argv = [docker, "compose"]
    if stack.project:
        argv += ["--project-name", stack.project]
    for name in stack.compose_files or [stack.compose_file]:
        argv += ["--file", str(resolve_under(workdir, name))]
    for profile in stack.profiles:
        argv += ["--profile", profile]
    for env_file in stack.env_files:
        argv += ["--env-file", str(resolve_under(workdir, env_file))]
    return argv


def teardown(ctx: Context, compose: list[str], policy: str, workdir: Path) -> str:
    if policy == "none":
        return "none"
    done = ctx.exec([*compose, policy], cwd=workdir, timeout=180, output=f"docker-{policy}.log")
    if done.ok:
        ctx.log(f"teardown: compose {policy} complete")
        return policy
    ctx.log(f"teardown warning: compose {policy} failed: {tail(done.stderr, 300)}")
    return f"{policy} failed"


def up(ctx: Context, compose: list[str], stack: PipelineStack, workdir: Path) -> StackResult:
    result = StackResult(ok=False, action="up", project=stack.project)
    listed = ctx.exec([*compose, "config", "--services"], cwd=workdir, timeout=60, output=None)
    known = listed.stdout.split() if listed.ok else []
    wanted = [s for wave in stack.waves for s in wave.services] or known
    try:
        if not listed.ok:
            raise ValueError(tail(listed.stderr or listed.stdout))
        if [s for s in wanted if s not in known]:
            raise ValueError(f"services not in the compose project (it has {', '.join(known)})")
        explicit = [w.services for w in stack.waves] or None
        plan = plan_waves(wanted, depends_on=stack.depends_on, explicit_waves=explicit)
    except (CycleError, ValueError) as exc:
        result.failed_step, result.error = "config", str(exc)
        return result
    result.services = list(plan.flat())
    for index, names in enumerate(plan.waves):
        declared = [w for w in stack.waves if names[0] in w.services]
        wave = declared[0] if declared else PipelineWave(f"wave-{index}", list(names))
        ctx.log(f"[docker] starting wave {wave.name}: {', '.join(names)}")
        argv = [*compose, "up", "--detach"]
        if stack.build:
            argv.append("--build")
        if stack.no_deps:
            argv.append("--no-deps")
        if wave.wait == "healthy":
            argv += ["--wait", "--wait-timeout", str(int(wave.timeout_s))]
        limit = None if stack.build else wave.timeout_s + 60
        done = ctx.exec(
            [*argv, *names], cwd=workdir, timeout=limit, output=f"docker-up-{wave.name}.log"
        )
        for name in names:
            logs = ctx.exec(
                [*compose, "logs", "--no-color", name],
                cwd=workdir,
                timeout=60,
                output=f"{name}.log",
            )
            if logs.artifact:
                result.logs[name] = logs.artifact
        if not done.ok:
            result.failed_step = wave.name
            result.error = "timed out" if done.timed_out else tail(done.stderr or done.stdout)
            result.teardown = teardown(ctx, compose, stack.teardown, workdir)
            return result
        result.waves_completed.append(wave.name)
        ctx.log(f"[docker] wave ready: {wave.name}")
    result.ok = True
    return result


@trestle(
    packages=["trestle_pack_docker", "trestle_pack_migrate", "trestle_pack_pytest"],
    summary_fields=["ok", "failed_stage"],
)
def stack_pipeline(
    ctx: Context,
    stack_spec: PipelineStack,
    alembic_config: str | None = None,
    pytest_path: str = "tests",
    workdir: str | None = None,
    python: str | None = None,
    markers: str | None = None,
    keyword: str | None = None,
) -> PipelineResult:
    """Run a Compose stack up, then optional Alembic migrations (`alembic_config`), then pytest
    (`pytest_path`), then tear the stack down once per `stack_spec.teardown` (volumes are kept).

    Paths are relative to `workdir`, the project root. Migrations and tests run on the project's
    interpreter (`python`, else its `.venv` or `venv`). A failed stage raises `Failed`
    (`<stage>_failed`) with the result (`ok: false`, `failed_stage`); each stage that ran has its
    own answer.
    """
    cwd = Path(workdir) if workdir else Path.cwd()
    stack = stack_spec
    interpreter = find_project_python(cwd, python)
    docker = locate_docker()
    info = ctx.exec([docker, "info", "--format", "{{.ServerVersion}}"], timeout=60, output=None)
    if not info.ok:
        raise Blocked(
            "docker_unreachable",
            "The Docker engine did not answer `docker info`. Start Docker (Docker Desktop, or "
            f"`colima start`), then resend. docker said: {tail(info.stderr, 300)}",
        )
    compose = compose_command(docker, stack, cwd)
    no_env = {"PYTHONPATH": None}

    ctx.log("stage: docker")
    stack_up = up(ctx, compose, stack, cwd)  # a failed `up` runs its own single teardown
    result = PipelineResult(ok=False, docker=stack_up)
    if not stack_up.ok:
        result.failed_stage, result.teardown = "docker", stack_up.teardown
        raise _failed(result, stack_up.error or "the stack did not come up")

    try:
        if alembic_config:
            ctx.log("stage: migrate")
            config = str(resolve_under(cwd, alembic_config))
            argv = [interpreter, "-m", "alembic", "-c", config, "upgrade", "head"]
            done = ctx.exec(argv, cwd=cwd, env=no_env, output="alembic-upgrade.log")
            result.migrate = MigrateResult(
                ok=done.ok, backend="alembic", target="head", python=interpreter,
                error=None if done.ok else tail(done.stderr or done.stdout),
            )  # fmt: skip
            if not done.ok:
                result.failed_stage = "migrate"
                raise _failed(result, result.migrate.error or "alembic upgrade failed")

        ctx.log("stage: pytest")
        junit = ctx.tmp / "pytest-junit.xml"
        junit.unlink(missing_ok=True)
        argv = [interpreter, "-m", "pytest", pytest_path, "-q", "--tb=short"]
        argv += ["-p", "no:cacheprovider", f"--junitxml={junit}", "-o", "junit_family=xunit1"]
        argv += (["-m", markers] if markers else []) + (["-k", keyword] if keyword else [])
        done = ctx.exec(argv, cwd=cwd, env=no_env, output="pytest-output.log")
        result.pytest = read_junit_report(
            junit, exit_code=done.returncode, duration_s=done.duration_s, python=interpreter
        ) or PytestReport(
            ok=False, exit_code=done.returncode, summary="pytest gave no report",
            error=tail(done.stderr or done.stdout), python=interpreter,
        )  # fmt: skip
        if junit.exists():
            ctx.attach(junit, name="pytest-junit.xml")
        result.ok = result.pytest.ok
        result.failed_stage = None if result.ok else "pytest"
        if not result.ok:
            raise _failed(result, result.pytest.summary or "pytest failed")
        return result
    finally:  # runs before a `Failed` leaves, so its result carries the teardown
        result.teardown = teardown(ctx, compose, stack.teardown, cwd)


def _failed(result: PipelineResult, detail: str) -> Failed:
    return Failed(f"{result.failed_stage}_failed", detail, result=result)
