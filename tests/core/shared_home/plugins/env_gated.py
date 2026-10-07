"""`gated` holding an environment (`env`): runs on one key start one at a time home-wide."""

from __future__ import annotations

import time
from pathlib import Path

from trestle.plugin.surface import Context, trestle

POLL_S = 0.02


@trestle(env_arg="env")
def env_gated(ctx: Context, env: str, gate: str, seconds: float = 30.0) -> dict[str, str]:
    path = Path(gate)
    deadline = time.monotonic() + seconds
    while not (path.exists() or (path.parent / "ALL").exists()):
        if ctx.cancelled or time.monotonic() > deadline:
            return {"gate": "closed"}
        time.sleep(POLL_S)
    return {"gate": "open"}
