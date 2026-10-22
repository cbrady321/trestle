"""Holds one environment for `seconds`, or until cancelled (agent surface 2b tests). Kept out of
tests/fixtures/plugins, which every McpHost seeds, so no catalog assertion elsewhere moves."""

from __future__ import annotations

import time

from trestle.plugin.surface import Context, trestle


@trestle(deadline=120, env_arg="env")
def env_hold(ctx: Context, env: str, seconds: float = 60.0) -> dict[str, bool]:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if ctx.cancelled:
            return {"cancelled": True}
        time.sleep(0.05)
    return {"done": True}
