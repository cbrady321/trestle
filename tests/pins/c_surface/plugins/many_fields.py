"""G-C4 fixture: an object with enough fields to overflow the 64 KiB index."""

from __future__ import annotations

from trestle.plugin.surface import Context, trestle


@trestle
def many_fields(ctx: Context, count: int = 4096) -> dict[str, int]:
    return {f"field_{i:06d}": i for i in range(count)}
