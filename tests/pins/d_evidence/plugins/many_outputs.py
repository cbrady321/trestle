"""Lane-D fixture plugin for G-D5: writes `count` files to work/outputs/
(auto-promoted). Kept under tests/pins/d_evidence/plugins/, never
tests/fixtures/plugins/ (S0 enumerates that directory)."""

from __future__ import annotations

from trestle.plugin.surface import Context, trestle


@trestle
def many_outputs(ctx: Context, count: int = 15) -> dict[str, int]:
    for i in range(count):
        (ctx.outputs / f"out-{i:03d}.txt").write_text(f"body-{i}", encoding="utf-8")
    return {"count": count}
