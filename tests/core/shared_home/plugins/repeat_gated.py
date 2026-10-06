"""Like `gated`, but declared repeatable (v0.4 step 3): when a run ends interrupted, re-sending its
idempotency key starts a fresh run."""

from __future__ import annotations

import time
from pathlib import Path

from trestle.plugin.surface import Context, trestle

POLL_S = 0.02


@trestle(repeatable=True)
def repeat_gated(ctx: Context, gate: str, seconds: float = 30.0) -> dict[str, str]:
    path = Path(gate)
    deadline = time.monotonic() + seconds
    while not (path.exists() or (path.parent / "ALL").exists()):
        if ctx.cancelled or time.monotonic() > deadline:
            return {"gate": "closed"}
        time.sleep(POLL_S)
    return {"gate": "open"}
