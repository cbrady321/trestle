"""Lane-D fixture plugin for G-D5: stages an artifact through
`ctx.artifact(...)` and returns without attaching it."""

from __future__ import annotations

from trestle.plugin.surface import Context, trestle

STAGED_BYTES = b"staged-partial-bytes"


@trestle
def staged_only(ctx: Context) -> dict[str, str]:
    staged = ctx.artifact("staged-report.bin")
    staged.write_bytes(STAGED_BYTES)
    return {"staged": staged.name}
