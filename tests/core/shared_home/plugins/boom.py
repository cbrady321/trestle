"""A plugin that raises (v0.4 step 5: a failed run's row in `run_by_key`)."""

from __future__ import annotations

from trestle.plugin.surface import Context, trestle


@trestle
def boom(ctx: Context, why: str = "no") -> dict[str, str]:
    raise RuntimeError(f"boom: {why}")
