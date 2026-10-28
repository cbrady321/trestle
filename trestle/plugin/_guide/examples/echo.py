"""The smallest plugin: one typed argument, one log line, a small mapping back."""

from __future__ import annotations

from trestle.plugin import Context, trestle


@trestle
def echo(ctx: Context, message: str = "hello") -> dict[str, str]:
    ctx.log(f"echo: {message}")
    return {"message": message}
