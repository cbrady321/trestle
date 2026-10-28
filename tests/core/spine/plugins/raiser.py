"""CS-3 fixture plugin: raises with a sentinel message, so a test can ask where it surfaces."""

from __future__ import annotations

from trestle.plugin.surface import Context, trestle

SENTINEL = "SENTINEL-cs3-raiser-91c4"


@trestle
def raiser(ctx: Context) -> dict[str, str]:
    raise RuntimeError(SENTINEL)
