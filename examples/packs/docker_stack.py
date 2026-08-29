"""Docker stack plugin — wave-based compose orchestration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from trestle_packs.docker.runner import StackRunner
from trestle_packs.docker.spec import StackSpec

from trestle.plugin.surface import Context, trestle


@trestle
def docker_stack(
    ctx: Context,
    spec: dict[str, Any] | None = None,
    compose_file: str = "docker-compose.yml",
    project: str | None = None,
    workdir: str | None = None,
) -> dict[str, Any]:
    """Bring up a Docker Compose stack in dependency waves.

    Pass a full ``spec`` dict (see hld/design-plugin-packs.md) or minimal
    ``compose_file`` + ``project`` with waves defined in the compose file.
    """
    if spec is None:
        spec = {
            "compose_file": compose_file,
            "project": project,
            "waves": [],
            "teardown": "down",
        }

    stack = StackSpec.from_dict(spec)
    cwd = Path(workdir) if workdir else Path.cwd()
    runner = StackRunner(ctx)
    result = runner.up(stack, cwd=cwd)
    return result.to_dict()
