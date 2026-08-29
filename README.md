# Trestle

A durable local execution ledger with an MCP control surface.

## Quick start

```bash
pip install -e ".[dev]"
trestle init
trestle doctor
```

## Agents (MCP)

Coding agents use **`trestle serve`** — ten frozen MCP tools (`run`, `query`, `fetch`, `publish_plugin`, …) over bounded run evidence. Wire Cursor or Claude Desktop via [`.cursor/mcp.json`](.cursor/mcp.json) (or equivalent `mcpServers` entry with `command: trestle`, `args: ["serve"]`).

Optional streamable HTTP (alongside stdio): `trestle serve --transport streamable-http` → `http://127.0.0.1:18732/mcp`. See [`hld/spec-agent-mcp-streamable-http.md`](hld/spec-agent-mcp-streamable-http.md).

**Agent guide:** [`docs/agents.md`](docs/agents.md) — quick start for coding agents.  
**Workflow packs:** [`docs/packs.md`](docs/packs.md) — Docker / pytest / migration plugins.  
**Full playbook:** [`docs/agent-console-mcp.md`](docs/agent-console-mcp.md) — retrieval tree, refusals, smoke checks.  
**Cursor skill:** [`.cursor/skills/trestle/SKILL.md`](.cursor/skills/trestle/SKILL.md) — compact rules for agents in this repo.

**Assessment:** [`hld/agent-mcp-usability-assessment.md`](hld/agent-mcp-usability-assessment.md).

## Operators (CLI + HTTP)

```bash
trestle doctor
trestle serve          # stdio MCP — agent path
trestle ops serve      # HTTP operator API — sessions/telemetry (localhost:18733)
trestle pin <run_id>
trestle recover
```

**Operator playbook:** [`docs/operator-sessions-telemetry.md`](docs/operator-sessions-telemetry.md) — bootstrap, UI routes, API examples, smoke checks.

**Operator spec:** [`hld/spec-operator-sessions-telemetry.md`](hld/spec-operator-sessions-telemetry.md).

```bash
trestle ops serve              # API + built UI at http://127.0.0.1:18733
# UI: /sessions (runs + failures), /registry, /host-wiring, /sessions/{id}/ask
# Dev UI (proxies /ops to ops serve):
cd console/web && npm install && npm run dev
```

## Requirements and architecture

- [`trestle-requirements.md`](trestle-requirements.md) — product requirements
- [`hld/hld-interface-architecture-trestle.md`](hld/hld-interface-architecture-trestle.md) — ten-tool freeze
- [`hld/plan-daytona-clean-room-scope.md`](hld/plan-daytona-clean-room-scope.md) — initiative closure (§11)
- [`docs/agents.md`](docs/agents.md) — agent guide (start here)
- [`docs/agent-console-mcp.md`](docs/agent-console-mcp.md) — agent playbook (full reference)
- [`hld/design-plugin-packs.md`](hld/design-plugin-packs.md) — workflow packs (Docker, pytest, migrations)
- [`docs/packs.md`](docs/packs.md) — pack agent quick-start
- [`docs/operator-sessions-telemetry.md`](docs/operator-sessions-telemetry.md) — operator playbook

## Verify

```bash
pytest -q                         # 142 tests
python scripts/smoke_agent_mcp.py
python scripts/smoke_operator_api.py
python scripts/smoke_packs.py     # pack plugins (requires pip install -e ".[packs]")
pytest tests/test_mcp_http_smoke.py -q   # optional HTTP MCP transport
```
