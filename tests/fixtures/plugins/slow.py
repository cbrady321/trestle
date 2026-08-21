"""Slow plugin for cancel and timeout tests."""

from __future__ import annotations

import time

from trestle.plugin.surface import Context, trestle


@trestle
def slow(ctx: Context, seconds: float = 30.0) -> dict[str, object]:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if ctx.cancelled:
            return {"cancelled": True}
        time.sleep(0.05)
    return {"done": True}
