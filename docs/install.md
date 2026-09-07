# Install and wire local agents

Install Trestle once on the machine, then attach Cursor, Claude Desktop, and Claude Code to the same stdio MCP server. External network clients cannot reach it — see [`security.md`](security.md).

## 1. Install

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pip install -e ".[packs]"          # optional: Docker / pytest / migration plugins
trestle init                       # creates ~/.trestle, seeds echo.py
chmod 700 ~/.trestle               # recommended — per-user home
trestle doctor                     # health: ok, plugins ≥ 1
```

Use the **absolute path** to the venv binary in MCP configs so hosts do not depend on `PATH`:

```bash
which trestle
# e.g. /Users/you/projects/trestle/.venv/bin/trestle
```

## 2. MCP snippet (all hosts)

```json
{
  "mcpServers": {
    "trestle": {
      "command": "/ABS/PATH/TO/.venv/bin/trestle",
      "args": ["serve"],
      "env": {
        "TRESTLE_HOME": "~/.trestle"
      }
    }
  }
}
```

With workflow packs for this repo:

```json
{
  "mcpServers": {
    "trestle": {
      "command": "/ABS/PATH/TO/.venv/bin/trestle",
      "args": [
        "serve",
        "--plugin-dir",
        "/ABS/PATH/TO/trestle/examples/packs"
      ],
      "env": {
        "TRESTLE_HOME": "~/.trestle"
      }
    }
  }
}
```

This repo ships [`.cursor/mcp.json`](../.cursor/mcp.json) as a **project** override (packs wired). For system-wide use, merge the snippet into the **user** config below.

## 3. Host configs

### Cursor

| Scope | File |
|-------|------|
| User (all workspaces) | `~/.cursor/mcp.json` |
| Project (this repo) | [`.cursor/mcp.json`](../.cursor/mcp.json) |

1. Merge the `trestle` entry into `mcpServers`.
2. Enable the server in Cursor MCP settings.
3. Confirm the host lists **ten** tools: `run`, `await_runs`, `cancel`, `query`, `fetch`, `pin`, `unpin`, `list_plugins`, `describe_plugin`, `publish_plugin`.

**Skill (optional):** copy [`.cursor/skills/trestle/`](../.cursor/skills/trestle/) to `~/.cursor/skills/trestle/` so other workspaces get the same agent rules.

### Claude Desktop

macOS config:

`~/Library/Application Support/Claude/claude_desktop_config.json`

Merge the same `mcpServers.trestle` object. Restart Claude Desktop. Confirm ten tools appear.

### Claude Code

Use the host’s user `mcpServers` config, or a project `.mcp.json`, with the same snippet. Prefer the absolute venv `trestle` path.

## 4. Optional streamable HTTP

Default is **stdio** (recommended). For hosts that cannot hold stdio:

```bash
trestle serve --transport streamable-http --port 18732
# endpoint: http://127.0.0.1:18732/mcp
```

```json
{
  "mcpServers": {
    "trestle-http": {
      "url": "http://127.0.0.1:18732/mcp"
    }
  }
}
```

Bind is fixed at `127.0.0.1`. There is no `--host` flag. Any process on the same machine can hit that port — prefer stdio when possible.

## 5. Verify

```bash
trestle doctor
python scripts/smoke_agent_mcp.py    # expect SMOKE OK
```

Host checklist:

- Ten MCP tools visible
- `list_plugins` returns at least `echo` (after `trestle init`)
- `run(plugin="echo", args={"message": "hi"}, wait_ms=5000)` returns a terminal `RunView`

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Host cannot start server / `command not found` | Use absolute path to `.venv/bin/trestle` |
| Empty catalog / `admission.plugin_not_found` | `trestle init`; check `catalog_hint` on `list_plugins` |
| Pack plugins `valid: false` | `pip install -e ".[packs]"`; see [`packs.md`](packs.md) |
| Wrong home / missing plugins | Set `TRESTLE_HOME` (or `--home`) to the directory you initialized |

## Next

- Agents: [`agents.md`](agents.md)
- Lockdown details: [`security.md`](security.md)
- Operators: [`operator-sessions-telemetry.md`](operator-sessions-telemetry.md)
