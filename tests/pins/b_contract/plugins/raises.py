"""G-B3 fixture: raises with a sentinel message."""

from __future__ import annotations

from trestle.plugin.surface import Context, trestle

SENTINEL = "SENTINEL-plugin-failure-7731"


@trestle
def raises(ctx: Context) -> dict[str, str]:
    raise RuntimeError(SENTINEL)
