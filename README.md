# Trestle

A durable local execution ledger with an MCP control surface. Coding agents run one-file Python plugins via ten frozen tools; evidence stays on disk and is pulled in bounded slices.

An agent runs a whole local job (a test suite, a Docker stack, a migration, a pipeline) as **one
tool call**, chains dependent jobs without polling, and reads a short answer instead of the log.
Plugins get safe building blocks for unattended work: `ctx.exec` (commands with a time limit,
process-tree kill and bounded output), `Blocked` (end a run with the one action a person must
take), a clean-stop window (`stop_grace`), live progress, and `trestle.plugin.testing` to test a
plugin without a server. Agent skill: [`skills/trestle/SKILL.md`](skills/trestle/SKILL.md).

## Install

Trestle runs as one service per user per machine (a macOS LaunchAgent or a `systemd --user`
unit); every agent host points at its loopback URL. From this checkout, install or upgrade to the
newest release with one command (`--drain` waits for live runs; `v0.4.0` names a release):

```bash
scripts/install.sh
```

To update later, `scripts/update.sh` fetches the newest release from GitHub (cloning into
`~/trestle` when run outside a checkout) and runs that release's own install script.

Or by hand:

```bash
python3 -m venv ~/.trestle-venv
~/.trestle-venv/bin/pip install -e ".[packs]"    # packs optional: Docker / pytest / migration plugins
~/.trestle-venv/bin/trestle init                 # ~/.trestle, echo.py, agent.token
~/.trestle-venv/bin/trestle service install      # write and load the unit; the service starts
```

`trestle service status` shows it running, with its endpoint and the installed and running build.
Upgrade with `git pull` then `trestle service upgrade` (`--drain` to wait for live runs).
`trestle service stop` / `start` / `restart` are the only ways to stop and start it. Full guide,
per host: [`docs/install.md`](docs/install.md).

## Wire every local agent

Endpoint `http://127.0.0.1:18732/mcp`; every request carries the agent token
(`$TRESTLE_HOME/agent.token`) as `Authorization: Bearer <token>`. Claude Code:

```bash
claude mcp add --scope user --transport http trestle http://127.0.0.1:18732/mcp \
  --header "Authorization: Bearer $(cat ~/.trestle/agent.token)"
```

Other hosts take the same `url` and `headers` in their MCP config:

```json
{
  "mcpServers": {
    "trestle": {
      "url": "http://127.0.0.1:18732/mcp",
      "headers": { "Authorization": "Bearer ${env:TRESTLE_AGENT_TOKEN}" }
    }
  }
}
```

| Host | User config |
|------|-------------|
| Cursor | `~/.cursor/mcp.json` (expands `${env:VAR}`) |
| Claude Desktop | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Claude Code | `claude mcp add` above, or `.mcp.json` (expands `${VAR}`) |

This repo's [`.cursor/mcp.json`](.cursor/mcp.json) is a project override. **Agent skill:** copy
[`skills/trestle/`](skills/trestle/) to your host's skills folder (`~/.claude/skills/trestle/` for
Claude Code, `~/.cursor/skills/trestle/` for Cursor) so agents use Trestle well in every workspace:
one call per objective, chained runs, bounded retrieval, unattended plugins.

Enable the server in the host UI. Confirm **ten** tools appear.

## Lockdown (external users cannot reach it)

- The MCP endpoint (`trestle serve`, run by the service) and the operator surface (`trestle ops serve`) bind **`127.0.0.1` only**. There is **no `--host` flag**. Do not port-forward or tunnel these ports.
- MCP requests without the agent token are refused (401); the token file is `0600`.
- Operator HTTP does **not** expose `run`.
- Keep `~/.trestle` private (`chmod 700`).

Details: [`docs/security.md`](docs/security.md).

## Agents (MCP)

Agents use the service's MCP endpoint and nothing else; an agent that finds it down reports that
and stops.

| Doc | Contents |
|-----|----------|
| [`docs/agents.md`](docs/agents.md) | Agent quick start |
| [`docs/agent-console-mcp.md`](docs/agent-console-mcp.md) | Full playbook — retrieval, refusals, smoke |
| [`docs/plugins.md`](docs/plugins.md) | Write and publish plugins |
| [`docs/packs.md`](docs/packs.md) | Docker / pytest / migration packs |
| [`skills/trestle/SKILL.md`](skills/trestle/SKILL.md) | Agent skill: one call per objective, chaining, `blocked` runs, plugins that run unattended |
| [`.cursor/skills/trestle/SKILL.md`](.cursor/skills/trestle/SKILL.md) | Compact Cursor skill |

## Operators (CLI + HTTP)

```bash
trestle service status # the service: running, pid, endpoint, builds
trestle doctor
trestle ops serve      # http://127.0.0.1:18733 — sessions / telemetry UI
trestle pin <run_id>
trestle cancel <run_id> # any live run, whatever session sent it
trestle recover
```

Playbook: [`docs/operator-sessions-telemetry.md`](docs/operator-sessions-telemetry.md).

## Docs

| Doc | Contents |
|-----|----------|
| [`docs/overview.md`](docs/overview.md) | What Trestle is and is not |
| [`docs/install.md`](docs/install.md) | The per-user service and host wiring |
| [`docs/security.md`](docs/security.md) | Local-only lockdown |
| [`docs/agents.md`](docs/agents.md) | Agent guide, including [large tasks as one tree run](docs/agents.md#large-tasks-the-tree) |
| [`docs/plugins.md`](docs/plugins.md) | Write and publish plugins: `ctx.exec`, `Blocked`, `stop_grace`, testing without a server, composite (tree) workflows |
| [`docs/packs.md`](docs/packs.md) | Install a pack: its library, its wrapper folder in `[plugins] paths`, `[plugins.env]` |
| [`docs/environment.md`](docs/environment.md) | The reference environment tree workflow |
| [`docs/decisions/service-and-credentials.md`](docs/decisions/service-and-credentials.md) | Decision record: one per-user service, credentials by name, MCP only (service, token, MCP-only and `ctx.exec` implemented) |
| [`docs/decisions/plain-plugin-stop-grace.md`](docs/decisions/plain-plugin-stop-grace.md) | Decision record: a clean-stop window for plain plugins |

## Verify

```bash
pytest -q
python scripts/smoke_agent_mcp.py
python scripts/smoke_operator_api.py
python scripts/smoke_packs.py              # requires pip install -e ".[packs]"
pytest tests/test_mcp_http_smoke.py -q     # the MCP endpoint, token included
```
