"""CS-2 fixture plugin: reads its stdin to EOF and reports how many bytes it got. A plugin
whose stdin is the host's MCP stdio transport would block, or take bytes that are not its own."""

from __future__ import annotations

import sys

from trestle.plugin.surface import Context, trestle


@trestle
def stdin_probe(ctx: Context) -> dict[str, int]:
    return {"stdin_bytes": len(sys.stdin.buffer.read())}
