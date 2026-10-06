"""A gate that writes its log to outputs/ first, then holds (v0.4 rule 3's promotion tests).
`token` is a declared secret: a call that gives it a value is a run with secret values."""

from __future__ import annotations

import time

from trestle.plugin.surface import Context, trestle


@trestle(secrets=["token"])
def gate_log(ctx: Context, seconds: float = 30.0, token: str = "") -> dict[str, bool]:
    (ctx.outputs / "pytest.log").write_text("collected 3 items\n3 passed\n", encoding="utf-8")
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if ctx.cancelled:
            return {"cancelled": True}
        time.sleep(0.05)
    return {"done": True}
