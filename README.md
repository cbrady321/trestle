# Trestle

A durable local execution ledger with an MCP control surface. Coding agents run one-file Python plugins via ten frozen tools; evidence stays on disk and is pulled in bounded slices.

## Install

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pip install -e ".[packs]"   # optional: Docker / pytest / migration plugins
trestle init
chmod 700 ~/.trestle
trestle doctor
```

Full host wiring (Cursor, Claude Desktop, Claude Code): [`docs/install.md`](docs/install.md).

## Wire every local agent

Use the **absolute** path to your venv’s `trestle` binary. Merge into the host’s **user** MCP config so every workspace can use it:

```json
{
  "mcpServers": {
    "trestle": {
      "command": "/ABS/PATH/TO/.venv/bin/trestle",
      "args": ["serve"],
      "env": { "TRESTLE_HOME": "~/.trestle" }
    }
  }
}
```

| Host | User config |
|------|-------------|
| Cursor | `~/.cursor/mcp.json` |
| Claude Desktop | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Claude Code | user `mcpServers` or project `.mcp.json` |

This repo’s [`.cursor/mcp.json`](.cursor/mcp.json) is a project override (packs wired). Copy [`.cursor/skills/trestle/`](.cursor/skills/trestle/) to `~/.cursor/skills/trestle/` for the same agent rules in other workspaces.

Enable the server in the host UI. Confirm **ten** tools appear.

## Lockdown (external users cannot reach it)

- **Default:** stdio MCP — only the spawning host talks to Trestle; no listen port.
- **HTTP** (`trestle serve --transport streamable-http`, `trestle ops serve`) binds **`127.0.0.1` only**. There is **no `--host` flag**. Do not port-forward or tunnel these ports.
- Operator HTTP does **not** expose `run`.
- Keep `~/.trestle` private (`chmod 700`).

Details: [`docs/security.md`](docs/security.md).

## Agents (MCP)

```bash
trestle serve          # stdio — recommended
```

Optional streamable HTTP (loopback only): `trestle serve --transport streamable-http` → `http://127.0.0.1:18732/mcp`.

| Doc | Contents |
|-----|----------|
| [`docs/agents.md`](docs/agents.md) | Agent quick start |
| [`docs/agent-console-mcp.md`](docs/agent-console-mcp.md) | Full playbook — retrieval, refusals, smoke |
| [`docs/plugins.md`](docs/plugins.md) | Write and publish plugins |
| [`docs/packs.md`](docs/packs.md) | Docker / pytest / migration packs |
| [`.cursor/skills/trestle/SKILL.md`](.cursor/skills/trestle/SKILL.md) | Compact Cursor skill |

## Operators (CLI + HTTP)

```bash
trestle doctor
trestle ops serve      # http://127.0.0.1:18733 — sessions / telemetry UI
trestle pin <run_id>
trestle recover
```

Playbook: [`docs/operator-sessions-telemetry.md`](docs/operator-sessions-telemetry.md).

## Docs

| Doc | Contents |
|-----|----------|
| [`docs/overview.md`](docs/overview.md) | What Trestle is and is not |
| [`docs/install.md`](docs/install.md) | System-wide install and host wiring |
| [`docs/security.md`](docs/security.md) | Local-only lockdown |

## Verify

```bash
pytest -q
python scripts/smoke_agent_mcp.py
python scripts/smoke_operator_api.py
python scripts/smoke_packs.py              # requires pip install -e ".[packs]"
pytest tests/test_mcp_http_smoke.py -q     # optional HTTP MCP transport
```
