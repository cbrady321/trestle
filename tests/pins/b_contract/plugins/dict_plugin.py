"""WR-COMPAT-4 fixture: dict-annotated parameter and return."""

from __future__ import annotations

from trestle.plugin.surface import Context, trestle


@trestle
def dict_plugin(ctx: Context, payload: dict[str, int]) -> dict[str, dict[str, int]]:
    is_dict = int(isinstance(payload, dict))
    return {
        "z": {"n": 1},
        "a": {"y": 2, "x": 3},
        "sum": {"total": sum(payload.values()), "isdict": is_dict},
    }
