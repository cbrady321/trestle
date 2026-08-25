# Spec — Agent MCP streamable HTTP (E6)

Status: **complete** — alongside stdio; same nine tools, same admission.  
Date: 2026-08-25  
Parent: [`trestle-requirements.md`](../trestle-requirements.md) transport amendment E6.

## Goals

- Optional **streamable HTTP** MCP transport for agents that cannot hold stdio.
- **stdio remains default** — R-FMC-1 unchanged for v0.1 default wiring.
- Same nine tools, identical admission — no ninth tool, no operator HTTP confusion.

## Non-goals

- Replacing stdio as default agent transport.
- FastMCP `http_app()` mount inside operator Starlette (R-FMC-4).
- Operator `/ops/v1` surface changes.

## Transport

| Command | Transport | Bind |
|---------|-----------|------|
| `trestle serve` | stdio (default) | — |
| `trestle serve --transport streamable-http` | streamable HTTP | `127.0.0.1:18732` default |

Endpoint: `http://127.0.0.1:{port}/mcp` (FastMCP default path).

**Loopback only** — non-loopback host rejected at CLI.

## Kernel

- MCP `run` / `await_runs` use async ControlSurface paths (`run_async`, `await_runs_async`).
- Sync `ControlSurface.run` retained for tests and operator in-process projection.
- Conductor `drive_async` — worker-thread dispatch; wrapper stays sync (R-EXEC-3).

## Host wiring (HTTP)

```json
{
  "mcpServers": {
    "trestle-http": {
      "url": "http://127.0.0.1:18732/mcp"
    }
  }
}
```

stdio snippet remains SSOT for Cursor default — see [`docs/agent-console-mcp.md`](../docs/agent-console-mcp.md).

## Verification

```bash
pytest tests/test_mcp_http_smoke.py -q
pytest tests/test_mcp_stdio_smoke.py -q
```

## References

- [`hld/plan-daytona-clean-room-scope.md`](plan-daytona-clean-room-scope.md) §11 E6
- [`docs/agent-console-mcp.md`](../docs/agent-console-mcp.md)
