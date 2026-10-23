"""The docker pack's library: the mechanism behind `docker_stack` and `stack_pipeline` (P-8).

The wrappers build every `docker compose` command line themselves and run it with `ctx.exec`;
this library finds the docker CLI, plans the start-up waves, waits within the run's budget and
holds the result shape. Everything an adapted wrapper may rely on is in `__all__`.
"""

from trestle_packs.core.dag import CycleError, WavePlan, plan_waves
from trestle_packs.core.paths import resolve_under
from trestle_packs.core.text import tail
from trestle_packs.docker.cli import (
    DOCKER_PATH_ENV,
    WELL_KNOWN_DOCKER,
    locate_docker,
    wait_until,
)
from trestle_packs.docker.results import StackResult

__all__ = [
    "DOCKER_PATH_ENV",
    "WELL_KNOWN_DOCKER",
    "CycleError",
    "StackResult",
    "WavePlan",
    "locate_docker",
    "plan_waves",
    "resolve_under",
    "tail",
    "wait_until",
]
