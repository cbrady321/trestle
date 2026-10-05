"""Plugin that returns a large array for projection tests."""

from __future__ import annotations

from trestle.plugin.surface import Context, trestle


@trestle
def big_array(ctx: Context, count: int = 10_000) -> list[int]:
    ctx.log(f"building array of {count}")
    return list(range(count))
