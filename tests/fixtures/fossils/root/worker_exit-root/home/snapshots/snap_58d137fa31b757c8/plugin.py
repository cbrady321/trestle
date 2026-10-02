"""Lane-A fixture plugin: exits with a code outside {0, 1}, so the wrapper
classifies the run `worker_exit` (fossil producer, L.P0-1A.5)."""

from __future__ import annotations

from trestle.plugin.surface import Context, trestle


@trestle
def exit2(ctx: Context) -> dict[str, bool]:
    raise SystemExit(2)
