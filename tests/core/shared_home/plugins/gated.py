"""A run that holds its slot until its gate file (or `ALL` beside it) exists (v0.4 step 1b's pool
tests): each test opens the gates one by one and sees which waiting run is granted next."""

from __future__ import annotations

import time
from pathlib import Path

from trestle.plugin.surface import Context, trestle


@trestle
def gated(ctx: Context, gate: str, seconds: float = 30.0) -> dict[str, str]:
    path = Path(gate)
    deadline = time.monotonic() + seconds
    while not (path.exists() or (path.parent / "ALL").exists()):
        if ctx.cancelled or time.monotonic() > deadline:
            return {"gate": "closed"}
        time.sleep(0.02)
    return {"gate": "open"}
