"""A plugin that raises: its run ends failed (run callbacks tests)."""

from __future__ import annotations

from trestle.plugin.surface import Context, trestle


@trestle
def boom(ctx: Context) -> dict[str, bool]:
    raise RuntimeError("boom")
