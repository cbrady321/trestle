"""CS-3 fixture plugin: leaves through SystemExit with a status the child passes through (the
wrapper reads a status above 1 as a worker exit)."""

from __future__ import annotations

from trestle.plugin.surface import Context, trestle


@trestle
def exiter(ctx: Context, status: int = 3) -> dict[str, str]:
    raise SystemExit(status)
