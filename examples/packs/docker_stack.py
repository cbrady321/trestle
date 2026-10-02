"""Docker stack plugin — wave-based compose orchestration."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from trestle_packs.docker.runner import StackResult, StackRunner
from trestle_packs.docker.spec import StackSpec

from trestle.plugin.surface import Context, trestle


@dataclass
class DockerProbe:
    kind: str = "command"
    command: list[str] = field(default_factory=list)


@dataclass
class DockerWave:
    name: str
    services: list[str]
    wait: str = "healthy"
    timeout_s: float = 120.0


@dataclass
class DockerSpec:
    project: str | None = None
    compose_file: str = "docker-compose.yml"
    teardown: str = "down"
    waves: list[DockerWave] = field(default_factory=list)
    probes: dict[str, DockerProbe] = field(default_factory=dict)
    depends_on: dict[str, list[str]] = field(default_factory=dict)


@trestle
def docker_stack(
    ctx: Context,
    spec: DockerSpec | None = None,
    compose_file: str = "docker-compose.yml",
    project: str | None = None,
    workdir: str | None = None,
) -> StackResult:
    """Bring up a Docker Compose stack in dependency waves.

    Pass a full ``spec`` dict (see docs/packs.md) or minimal
    ``compose_file`` + ``project`` with waves defined in the compose file.
    """
    if spec is None:
        raw: dict[str, Any] = {
            "compose_file": compose_file,
            "project": project,
            "waves": [],
            "teardown": "down",
        }
    elif isinstance(spec, dict):
        raw = spec
    else:
        raw = asdict(spec)

    stack = StackSpec.from_dict(raw)
    cwd = Path(workdir) if workdir else Path.cwd()
    runner = StackRunner(ctx)
    result = runner.up(stack, cwd=cwd)
    return result.to_dict()
