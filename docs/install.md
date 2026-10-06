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

<!-- K-14 -->
**Type checking.** `mypy` runs at zero errors and is gated in CI by its own `typecheck` job. The gate needs the workflow packs installed (`pip install -e ".[dev,packs]"`): `trestle_packs` ships a `py.typed` marker so `import trestle_packs` type-checks, and with `.[dev]` alone the package is absent and `mypy` reports `import-not-found`. Run it locally the same way: `pip install -e ".[dev,packs]" && mypy`.
<!-- /K-14 -->

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

`--port 0` picks a free port and prints `trestle: listening 127.0.0.1:<port>` on stderr once it accepts connections (useful for scripts and tests that must not collide on a fixed port).

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
- `run(plugin="echo", args={"message": "hi"}, wait_ms=5000, completion="terminal")` also does, and can never return a `running` frame

## Several servers on one home

Any number of `trestle serve` (and `trestle ops serve`) processes may share one `TRESTLE_HOME`.
The home must be on a local file system: every entry point (`serve`, `ops serve`, `init`,
`recover`, `doctor`, `pin`, `unpin`) refuses an nfs, smbfs, afpfs, webdav or cifs mount. A home
records its format in `home/format` (2); a home a v0.3.0 server used is refused until it is
upgraded, and v0.3.0 and v0.4 servers must never share a home:

```bash
# stop every v0.3.0 server on the home first
trestle init --upgrade
```

`trestle init` never recovers runs. Idempotency keys live in `home/keys/` (one file per key, each
run's expiry fixed at admission); nothing rebuilds them at start, `init --upgrade` seeds them from
the runs (a key that had expired stays expired), and `trestle doctor --rebuild-keys` repairs them.
`trestle recover` reaps now (only runs whose server is gone) and then runs the retention sweep; `trestle doctor` lists the live servers, the live runs by owner
and, when an admission lock is stuck (a server stopped with Ctrl-Z or a debugger), who holds it.
A server stopped with SIGTERM drains: it refuses new runs and keeps starting and finishing the
runs it admitted; stop it once `doctor` shows it has no live runs. All servers on a home share one
pool of `max_running_runs` slots (31 by default: three servers no longer get 93), fairly, with one
slot kept free for each other idle server; see [Run capacity](agents.md#run-capacity).

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Host cannot start server / `command not found` | Use absolute path to `.venv/bin/trestle` |
| Empty catalog / `admission.plugin_not_found` | `trestle init`; check `catalog_hint` on `list_plugins` |
| Pack plugins `valid: false` | `pip install -e ".[packs]"`; see [`packs.md`](packs.md) |
| Wrong home / missing plugins | Set `TRESTLE_HOME` (or `--home`) to the directory you initialized |
| `a v0.3.0 home` | Stop every v0.3.0 server, then `trestle init --upgrade` |
| `admission.home_busy` | Another process held the home's admission lock over 2 s; retry, and see `trestle doctor` |
| `admission.after_unknown` | `run(after=...)` names a run or key no run used |
| `admission.ttl_out_of_range` | `idempotency_ttl_s` is negative, not whole seconds or above `[keys] max_ttl_s` in `config.toml` (default 604800) |

## Next

- Agents: [`agents.md`](agents.md)
- Lockdown details: [`security.md`](security.md)
- Operators: [`operator-sessions-telemetry.md`](operator-sessions-telemetry.md)
