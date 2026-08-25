# Trestle

A durable local execution ledger with an MCP control surface.

## Quick start

```bash
pip install -e ".[dev]"
mkdir -p ~/.trestle/plugins
cp examples/plugins/echo.py ~/.trestle/plugins/
trestle doctor
```

## Agents (MCP)

Coding agents use **`trestle serve`** — nine frozen MCP tools (`run`, `query`, `fetch`, …) over bounded run evidence. Wire Cursor or Claude Desktop via [`.cursor/mcp.json`](.cursor/mcp.json) (or equivalent `mcpServers` entry with `command: trestle`, `args: ["serve"]`).

**Playbook:** [`docs/agent-console-mcp.md`](docs/agent-console-mcp.md) — attach, golden workflow, retrieval tree, refusals, smoke checks.

**Assessment:** [`hld/agent-mcp-usability-assessment.md`](hld/agent-mcp-usability-assessment.md).

## Operators (CLI)

```bash
trestle doctor
trestle serve          # stdio MCP — agent path
trestle ops serve      # HTTP operator API — sessions/telemetry (localhost:18733)
trestle pin <run_id>
trestle recover
```

**Operator HTTP:** [`hld/spec-operator-sessions-telemetry.md`](hld/spec-operator-sessions-telemetry.md) — retrieval-first sessions + telemetry UI (Phase D/E).

## Requirements and architecture

- [`trestle-requirements.md`](trestle-requirements.md) — product requirements
- [`hld/hld-interface-architecture-trestle.md`](hld/hld-interface-architecture-trestle.md) — nine-tool freeze
- [`hld/plan-daytona-clean-room-scope.md`](hld/plan-daytona-clean-room-scope.md) — active scope (Phase E)
- [`docs/agent-console-mcp.md`](docs/agent-console-mcp.md) — agent playbook

## Verify

```bash
pytest -q                    # 109 tests
python scripts/smoke_agent_mcp.py
```
