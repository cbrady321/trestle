"""Fixture plugin with two @trestle callables (G-B1).

`zeta` comes first in source order, `alpha` first alphabetically. The schema
deriver takes the first marked callable in source order; the child takes the
first in `dir()` (alphabetical) order.
"""

from __future__ import annotations

from trestle.plugin.surface import Context, trestle


@trestle
def zeta(ctx: Context, message: str = "z") -> dict[str, str]:
    return {"ran": "zeta", "message": message}


@trestle
def alpha(ctx: Context, message: str = "a") -> dict[str, str]:
    return {"ran": "alpha", "message": message}
