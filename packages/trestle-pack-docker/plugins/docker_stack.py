"""docker_stack: bring a Docker Compose stack up in dependency waves, or stop it or take it down.

The recipe, top to bottom (adapt it by copying this file and publishing it under a new name):

1. find the docker CLI and check the engine answers `docker info` (`Blocked` when not);
2. `docker compose <project, files, profiles, env files> config --services` lists the services;
3. `up`: each wave runs `docker compose ... up --detach [--build] [--no-deps] [--wait] <services>`,
   waits on its custom probes, and saves each service's `docker compose logs` as an artifact.
   A failed wave runs the declared teardown once and the answer is `ok: false`;
4. `stop`: `docker compose ... stop [services]`; `down`: `docker compose ... down`.

No teardown removes a volume: `down` never passes `--volumes`. Every command goes through
`ctx.exec`; finding docker, planning waves and waiting are `trestle_pack_docker`.

Minimal form: `{"workdir": "/abs/project"}` starts every service of its `docker-compose.yml`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from trestle_pack_docker import (
    CycleError,
    locate_docker,
    plan_waves,
    resolve_under,
    tail,
    wait_until,
)
from trestle_pack_docker.results import StackResult  # where it is defined: the schema reads it

from trestle.plugin import Blocked, Context, trestle


@dataclass
class DockerProbe:
    """A host command that exits 0 once its service is ready (for `wait: custom`)."""

    kind: Literal["command"] = "command"
    command: list[str] = field(default_factory=list)


@dataclass
class DockerWave:
    """Services started together; the next wave starts once this one is ready."""

    name: str
    services: list[str]
    wait: Literal["healthy", "started", "custom"] = "healthy"
    timeout_s: float = 120.0


@dataclass
class DockerSpec:
    """The stack. `compose_files` (in order) replaces `compose_file` when given. Services in no
    wave start in waves planned from `depends_on`, waiting `wait` up to `timeout_s`."""

    project: str | None = None
    compose_file: str = "docker-compose.yml"
    compose_files: list[str] = field(default_factory=list)
    profiles: list[str] = field(default_factory=list)
    env_files: list[str] = field(default_factory=list)
    build: bool = False
    no_deps: bool = False
    teardown: Literal["down", "stop", "none"] = "down"
    waves: list[DockerWave] = field(default_factory=list)
    depends_on: dict[str, list[str]] = field(default_factory=dict)
    probes: dict[str, DockerProbe] = field(default_factory=dict)
    wait: Literal["healthy", "started", "custom"] = "healthy"
    timeout_s: float = 120.0


def compose_command(docker: str, stack: DockerSpec, workdir: Path) -> list[str]:
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


def check_engine(ctx: Context, docker: str) -> None:
    info = ctx.exec([docker, "info", "--format", "{{.ServerVersion}}"], timeout=60, output=None)
    if not info.ok:
        raise Blocked(
            "docker_unreachable",
            "The Docker engine did not answer `docker info`. Start Docker (Docker Desktop, or "
            f"`colima start`), then resend. docker said: {tail(info.stderr, 300)}",
        )
    compose = ctx.exec([docker, "compose", "version", "--short"], timeout=60, output=None)
    if not compose.ok:
        raise Blocked(
            "docker_compose_missing",
            "`docker compose` is not available. Install the Docker Compose plugin, then resend.",
        )


def teardown(ctx: Context, compose: list[str], policy: str, workdir: Path) -> str:
    """The declared teardown, once: `down` (containers, networks), `stop`, or `none`."""
    if policy == "none":
        return "none"
    done = ctx.exec([*compose, policy], cwd=workdir, timeout=180, output=f"docker-{policy}.log")
    if done.ok:
        ctx.log(f"teardown: compose {policy} complete")
        return policy
    ctx.log(f"teardown warning: compose {policy} failed: {tail(done.stderr, 300)}")
    return f"{policy} failed"


def up(
    ctx: Context, compose: list[str], stack: DockerSpec, only: list[str], workdir: Path
) -> StackResult:
    result = StackResult(ok=False, action="up", project=stack.project)
    listed = ctx.exec([*compose, "config", "--services"], cwd=workdir, timeout=60, output=None)
    if not listed.ok:
        result.failed_step, result.error = "config", tail(listed.stderr or listed.stdout)
        return result
    known = listed.stdout.split()
    wanted = only or [s for wave in stack.waves for s in wave.services] or known
    unknown = [s for s in wanted if s not in known]
    custom = [w for w in stack.waves if w.wait == "custom"]
    unprobed = [
        s for w in custom for s in w.services if not stack.probes.get(s, DockerProbe()).command
    ]
    if unknown or unprobed:
        result.failed_step = "config"
        result.error = (
            f"services not in the compose project: {', '.join(unknown)} (it has {', '.join(known)})"
            if unknown
            else f"wait 'custom' needs probes for: {', '.join(unprobed)}"
        )
        return result
    explicit = [[s for s in w.services if s in wanted] for w in stack.waves]
    depends = {k: [d for d in v if d in wanted] for k, v in stack.depends_on.items() if k in wanted}
    try:
        plan = plan_waves(
            wanted, depends_on=depends, explicit_waves=[w for w in explicit if w] or None
        )
    except (CycleError, ValueError) as exc:
        result.failed_step, result.error = "config", str(exc)
        return result
    result.services = list(plan.flat())

    for index, names in enumerate(plan.waves):
        declared = [w for w in stack.waves if names[0] in w.services]
        wave = (
            declared[0]
            if declared
            else DockerWave(f"wave-{index}", [], stack.wait, stack.timeout_s)
        )
        ctx.progress(
            f"[docker] starting wave {wave.name}: {', '.join(names)}",
            fraction=index / len(plan.waves),
        )
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
        error = (
            None
            if done.ok
            else ("timed out" if done.timed_out else tail(done.stderr or done.stdout))
        )
        if error is None and wave.wait == "custom":
            for name in names:
                probe = stack.probes[name].command
                ready = wait_until(
                    ctx,
                    lambda probe=probe: ctx.exec(probe, cwd=workdir, timeout=30, output=None).ok,
                    timeout_s=wave.timeout_s,
                )
                if not ready:
                    error = f"probe for {name} did not pass within {wave.timeout_s}s: {probe}"
                    break
        for name in names:
            logs = ctx.exec(
                [*compose, "logs", "--no-color", name],
                cwd=workdir,
                timeout=60,
                output=f"{name}.log",
            )
            if logs.artifact:
                result.logs[name] = logs.artifact
        if error is not None:
            result.failed_step, result.error = wave.name, error
            ctx.log(f"[docker] wave failed: {wave.name}: {error}")
            result.teardown = teardown(ctx, compose, stack.teardown, workdir)
            return result
        result.waves_completed.append(wave.name)
        ctx.log(f"[docker] wave ready: {wave.name}")
    result.ok = True
    return result


@trestle(packages=["trestle_pack_docker"], summary_fields=["ok", "failed_step", "error"])
def docker_stack(
    ctx: Context,
    spec: DockerSpec | None = None,
    compose_file: str = "docker-compose.yml",
    project: str | None = None,
    workdir: str | None = None,
    action: Literal["up", "stop", "down"] = "up",
    services: list[str] | None = None,
) -> StackResult:
    """Bring a Compose stack up in dependency waves (`action: up`), stop services (`stop`, all or
    the named `services`) or take it down (`down`: containers and networks; volumes are kept).

    Pass a full `spec`, or just `workdir` (plus `compose_file`, `project`) to start every service
    of the compose file. `services` limits `up` and `stop` to those services. A stack that does
    not come up is `ok: false` with the failed step, docker's error and the per-service logs.
    """
    stack = spec or DockerSpec(project=project, compose_file=compose_file)
    stack.project = stack.project or project
    cwd = Path(workdir) if workdir else Path.cwd()
    absent = [
        f
        for f in stack.compose_files or [stack.compose_file]
        if not resolve_under(cwd, f).is_file()
    ]
    if absent:
        return StackResult(
            ok=False, action=action, project=stack.project, failed_step="config",
            error=f"compose file not found: {resolve_under(cwd, absent[0])} (workdir {cwd})",
        )  # fmt: skip
    docker = locate_docker()
    check_engine(ctx, docker)
    compose = compose_command(docker, stack, cwd)
    if action == "up":
        return up(ctx, compose, stack, services or [], cwd)
    if action == "down" and services:
        return StackResult(
            ok=False, action=action, project=stack.project, failed_step="down",
            error="down takes the whole stack; use action 'stop' with services",
        )  # fmt: skip
    argv = [*compose, action, *(services or [])]
    done = ctx.exec(argv, cwd=cwd, timeout=180, output=f"docker-{action}.log")
    return StackResult(
        ok=done.ok, action=action, project=stack.project, services=services or [],
        failed_step=None if done.ok else action, error=None if done.ok else tail(done.stderr),
    )  # fmt: skip
