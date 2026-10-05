"""CS-4 fixture plugin: declares a JSON-shaped result and returns a value the encoder cannot
encode (WR-COMPAT-4)."""

from __future__ import annotations

from typing import cast

from trestle.plugin.surface import Context, trestle


@trestle
def unencodable(ctx: Context) -> dict[str, int]:
    return cast("dict[str, int]", {"value": object()})
