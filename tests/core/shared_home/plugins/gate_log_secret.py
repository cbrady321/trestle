"""`gate_log` with a credential identity: a run of it is a run with credential values, so a
reaped one keeps nothing (v0.3.1 rule 3). Its tests grant it a fake value; elsewhere it is
refused at admission and never runs."""

from __future__ import annotations

import time

from trestle.plugin.surface import Context, trestle

POLL_S = 0.05


@trestle(credentials=["gate-token"])
def gate_log_secret(ctx: Context, seconds: float = 30.0) -> dict[str, bool]:
    (ctx.outputs / "pytest.log").write_text("collected 3 items\n3 passed\n", encoding="utf-8")
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if ctx.cancelled:
            return {"cancelled": True}
        time.sleep(POLL_S)
    return {"done": True}
