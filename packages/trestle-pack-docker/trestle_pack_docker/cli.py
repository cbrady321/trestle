"""Finding the docker CLI and waiting within a run's budget (the docker pack's mechanism)."""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from trestle.plugin import Blocked
from trestle_pack_docker.readiness import PollCancelled, PollConfig, poll_until

DOCKER_PATH_ENV = "TRESTLE_DOCKER_PATH"
# Where Docker Desktop, Homebrew, Colima and the distribution packages put the CLI: a launchd or
# systemd service often runs with a PATH that has none of them.
WELL_KNOWN_DOCKER = (
    "/usr/local/bin/docker",
    "/opt/homebrew/bin/docker",
    "/Applications/Docker.app/Contents/Resources/bin/docker",
    "/usr/bin/docker",
)


def _runnable(path: str) -> bool:
    return Path(path).is_file() and os.access(path, os.X_OK)


def locate_docker(environ: Mapping[str, str] | None = None) -> str:
    """The docker CLI's absolute path: `TRESTLE_DOCKER_PATH` when set, else the first `docker` on
    PATH, else a well-known install location. None found raises `Blocked`."""
    env = os.environ if environ is None else environ
    named = env.get(DOCKER_PATH_ENV)
    if named:
        if _runnable(named):
            return named
        raise Blocked(
            "docker_missing",
            f"{DOCKER_PATH_ENV}={named} is not an executable. Fix it under [plugins.env] in "
            "$TRESTLE_HOME/config.toml (or remove it to use the docker on PATH), then resend.",
        )
    found = shutil.which("docker", path=env.get("PATH"))
    if found:
        return found
    for candidate in WELL_KNOWN_DOCKER:
        if _runnable(candidate):
            return candidate
    raise Blocked(
        "docker_missing",
        "No docker CLI found. Install Docker (Docker Desktop, Colima, or Docker Engine with the "
        f"compose plugin), or set {DOCKER_PATH_ENV} to its absolute path under [plugins.env] in "
        "$TRESTLE_HOME/config.toml; then resend.",
    )


class _Cancelled:
    def __init__(self, ctx: Any) -> None:
        self._ctx = ctx

    def is_set(self) -> bool:
        return bool(self._ctx.cancelled)


def wait_until(
    ctx: Any,
    check: Callable[[], bool],
    *,
    timeout_s: float,
    interval_s: float = 2.0,
    sleep: Callable[[float], None] | None = None,
) -> bool:
    """Call `check` until it returns True (True), or until `timeout_s`, the run's remaining time
    or a stop of the run comes first (False). Never raises for a timeout or a stop."""
    budget = max(0.0, min(timeout_s, ctx.remaining()))
    try:
        poll_until(
            check,
            config=PollConfig(timeout_s=budget, interval_s=interval_s),
            sleep=sleep,
            cancel=_Cancelled(ctx),
        )
    except (TimeoutError, PollCancelled):
        return False
    return True
