"""Lane-A fixture plugin: floods stdout past the console cap (run under
`TRESTLE_TEST_LIMITS=1`), so the run finalizes `partial` with a
`limit_exceeded` row (fossil producer, L.P0-1A.5)."""

from __future__ import annotations

from trestle.plugin.surface import Context, trestle


@trestle
def noisy(ctx: Context, lines: int = 400) -> dict[str, int]:
    for i in range(lines):
        print(f"noisy line {i:06d} " + "x" * 40)
    return {"lines": lines}
