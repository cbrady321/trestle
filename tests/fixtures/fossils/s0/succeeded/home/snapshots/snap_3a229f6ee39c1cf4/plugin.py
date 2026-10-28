"""Hostile plugin for R-VER-2 control-plane responsiveness tests."""

from __future__ import annotations

from dataclasses import dataclass

from trestle.plugin.surface import Context, trestle


@dataclass
class HostileResult:
    items: list[int]
    note: str


@trestle
def hostile(ctx: Context, *, flood_lines: int = 500, flood_events: int = 200) -> HostileResult:
    for i in range(flood_lines):
        print(f"flood-line-{i}-" + ("x" * 64))
    for i in range(flood_events):
        ctx.log(f"event-{i}-" + ("y" * 32))
    return {"items": list(range(5000)), "note": "hostile"}
