"""The docker pack's library: the mechanism behind `docker_stack` and `stack_pipeline` (P-8).

The wrappers build every `docker compose` command line themselves and run it with `ctx.exec`;
this library finds the docker CLI, plans the start-up waves, waits within the run's budget and
holds the result shape. Everything an adapted wrapper may rely on is in `__all__`.
"""

from trestle_pack_docker.cli import (
    DOCKER_PATH_ENV,
    WELL_KNOWN_DOCKER,
    locate_docker,
    wait_until,
)
from trestle_pack_docker.dag import CycleError, WavePlan, plan_waves
from trestle_pack_docker.paths import resolve_under
from trestle_pack_docker.results import StackResult
from trestle_pack_docker.text import tail

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
