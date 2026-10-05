"""G-C4 fixture: an object whose fields are inserted out of key order."""

from __future__ import annotations

from trestle.plugin.surface import Context, trestle


@trestle
def sorted_fill(ctx: Context, width: int = 200) -> dict[str, str]:
    filler = "z" * width
    return {"k3": filler, "k1": filler, "k4": "s", "k2": filler}
