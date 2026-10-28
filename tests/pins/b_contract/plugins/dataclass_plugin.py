"""G-B2 fixture: dataclass-annotated parameter; reports what it received."""

from __future__ import annotations

from dataclasses import dataclass

from trestle.plugin.surface import Context, trestle


@dataclass
class Point:
    x: int
    y: int


@trestle
def dataclass_plugin(ctx: Context, point: Point) -> dict[str, str]:
    return {"received": type(point).__name__}
