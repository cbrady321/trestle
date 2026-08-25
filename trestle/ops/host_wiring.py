"""Operator host wiring — static MCP stdio snippet for copy-paste."""

from __future__ import annotations

HOST_WIRING_SNIPPET = {
    "mcpServers": {
        "trestle": {
            "command": "trestle",
            "args": ["serve"],
            "env": {
                "TRESTLE_HOME": "~/.trestle",
            },
        },
    },
}


def read_host_wiring() -> dict[str, object]:
    return {
        "transport": "stdio",
        "snippet": HOST_WIRING_SNIPPET,
        "note": "Agent path only — operator HTTP does not replace stdio MCP.",
    }
