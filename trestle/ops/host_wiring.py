"""Operator host wiring: the static MCP snippet an agent host merges into its config.

The snippet names the service's loopback URL and the agent token by environment variable, never
by value: the ops port carries no token of its own, so it must not hand out the one the MCP port
checks (`$TRESTLE_HOME/agent.token`)."""

from __future__ import annotations

from trestle.server.agent_token import TOKEN_ENV
from trestle.server.main import DEFAULT_PORT

HOST_WIRING_SNIPPET = {
    "mcpServers": {
        "trestle": {
            "type": "http",
            "url": f"http://127.0.0.1:{DEFAULT_PORT}/mcp",
            "headers": {"Authorization": f"Bearer ${{{TOKEN_ENV}}}"},
        },
    },
}


def read_host_wiring() -> dict[str, object]:
    return {
        "transport": "streamable-http",
        "snippet": HOST_WIRING_SNIPPET,
        "note": (
            f"Agent path only: the trestle service's MCP endpoint. Set {TOKEN_ENV} to the "
            "contents of $TRESTLE_HOME/agent.token, or paste the token in place of the variable."
        ),
    }
