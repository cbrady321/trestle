"""Lane-D fixture plugin: raises with a known sentinel message, so a test can
ask where that message surfaces (G-D3). Kept under tests/pins/d_evidence/
plugins/, never tests/fixtures/plugins/ (S0 enumerates that directory)."""

from __future__ import annotations

from trestle.plugin.surface import Context, trestle

SENTINEL = "SENTINEL-d3-explanation-7c1f"


@trestle
def sentinel_fail(ctx: Context) -> dict[str, str]:
    raise RuntimeError(SENTINEL)
