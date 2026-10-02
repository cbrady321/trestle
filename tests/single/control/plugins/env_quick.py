"""L.SL-8.2 fixture plugin: an environment plugin that returns at once (the later run admitted
after a holder's answer)."""

from __future__ import annotations

from trestle.plugin.surface import Context, trestle


@trestle(env_arg="env")
def env_quick(ctx: Context, env: str = "dev") -> dict[str, str]:
    return {"env": env}
