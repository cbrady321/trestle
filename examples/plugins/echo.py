"""Example plugin — copy to ~/.trestle/plugins/ for agent MCP smoke tests."""

from __future__ import annotations

from trestle.plugin.surface import Context, trestle


@trestle
def echo(ctx: Context, message: str = "hello") -> dict[str, str]:
    ctx.log(f"echo: {message}")
    return {"message": message}
