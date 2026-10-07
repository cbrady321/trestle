"""Trivial echo plugin for M1 smoke tests."""

from __future__ import annotations

from trestle.plugin.surface import Context, trestle


@trestle
def echo(ctx: Context, message: str = "mutated") -> dict[str, str]:
    ctx.log(f"echo: {message}")
    return {"message": message}
