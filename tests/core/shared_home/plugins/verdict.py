"""A plugin that answers `{"ok": ok, "n": n}`, after its gate file exists when it is given one
(v0.3.1 step 6: the earlier run of a chain, whose result `after.match` reads)."""

from __future__ import annotations

import time
from pathlib import Path

from trestle.plugin.surface import Context, trestle

POLL_S = 0.02


@trestle
def verdict(
    ctx: Context, ok: bool = True, n: int = 1, gate: str | None = None, seconds: float = 30.0
) -> dict[str, bool | int]:
    if gate is not None:
        path = Path(gate)
        deadline = time.monotonic() + seconds
        while not (path.exists() or (path.parent / "ALL").exists()):
            if ctx.cancelled or time.monotonic() > deadline:
                return {"ok": False, "n": -1}
            time.sleep(POLL_S)
    return {"ok": ok, "n": n}
