"""CS-4 fixture plugin: ignores SIGTERM and keeps working, so a stop can only end it with SIGKILL
after the grace (PC-19: a deadline stop of such a plugin still has to answer inside the bound)."""

from __future__ import annotations

import signal
import time

from tests.proof.tolerances import POLL_FINE_S
from trestle.plugin.surface import Context, trestle


@trestle
def sigterm_ignorer(ctx: Context, seconds: float = 600.0) -> dict[str, bool]:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    (ctx.tmp / "ready").write_text("1", encoding="utf-8")
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        time.sleep(POLL_FINE_S)
    return {"done": True}
