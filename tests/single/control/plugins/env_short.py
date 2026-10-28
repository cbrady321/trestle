"""L.SL-8.2 fixture plugin: an environment plugin with a short declared deadline, so its would-be
deadline can fall before the deadline of a run that already holds the same environment."""

from __future__ import annotations

from trestle.plugin.surface import Context, trestle


@trestle(env_arg="env", deadline=60)
def env_short(ctx: Context, env: str = "dev") -> dict[str, str]:
    return {"env": env}
