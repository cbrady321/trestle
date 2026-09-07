# Local-only lockdown

Trestle is a **single-user, localhost** product. External network clients must not be able to reach the MCP or operator surfaces.

## What is locked down

| Surface | Exposure |
|---------|----------|
| `trestle serve` (stdio, default) | Process-local. Only the spawning MCP host talks to Trestle. No listen port. |
| `trestle serve --transport streamable-http` | Binds **`127.0.0.1` only** (default port **18732**). No `--host` flag. |
| `trestle ops serve` | Binds **`127.0.0.1` only** (default port **18733**). No `--host` flag. Read-only — does **not** expose `run`. |
| `$TRESTLE_HOME` (default `~/.trestle`) | Per-user ledger and plugins. Recommend `chmod 700 ~/.trestle`. |

CLI rejects `--host` (including `0.0.0.0`). Do not port-forward, tunnel, or reverse-proxy these ports to other machines.

## Prefer stdio

Stdio is the default agent path. Streamable HTTP is optional for hosts that cannot hold a stdio child. On HTTP, **any local process** on the machine can connect to `127.0.0.1:18732` — still unreachable from other hosts, but weaker isolation than stdio.

## What “local-only” does not mean

- Plugins run **as your user**. They can read/write files, spawn processes, and make network calls the OS allows.
- Trestle **bounds what reaches the agent** (summaries, named views, fetch windows). It is **not** a sandbox that isolates plugins from the host.
- Operator UI and HTTP MCP are loopback-only; they are not authenticated multi-user services.

## Operator vs agent

- **Agents** use MCP (`trestle serve`) — can admit and `run` work.
- **Operators** use CLI (`doctor`, `pin`, `unpin`, `recover`) and optional `trestle ops serve` — browse sessions and telemetry; no admit/`run` from the browser.

## Related

- Install and host wiring: [`install.md`](install.md)
- Product overview: [`overview.md`](overview.md)
