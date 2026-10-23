"""Slow plugin for cancel and timeout tests."""

from __future__ import annotations

import time

from trestle.plugin.surface import Context, trestle

POLL_S = 0.05


@trestle
def slow(ctx: Context, seconds: float = 30.0) -> dict[str, bool]:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if ctx.cancelled:
            return {"cancelled": True}
        time.sleep(POLL_S)
    return {"done": True}
