"""Plugin that writes to work/outputs/ for auto-promote tests."""

from __future__ import annotations

from trestle.plugin.surface import Context, trestle


@trestle
def outputs_writer(
    ctx: Context,
    name: str = "report.txt",
    body: str = "hello-output",
) -> dict[str, str]:
    path = ctx.outputs / name
    path.write_text(body, encoding="utf-8")
    return {"output": name}
