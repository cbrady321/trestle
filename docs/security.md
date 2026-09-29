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

## What stopping a run does not cover

Trestle ends the processes it can **attribute** to a run: the run's recorded
leader, its recorded process group, and the descendants it observed by parent
id, wherever they have moved since. Two boundaries sit outside that guarantee,
and Trestle does not claim to contain more than this.

1. **A double fork between two snapshots.** Trestle watches a run's process
   ancestry by taking snapshots. A descendant that double-forks out of
   attribution between two snapshots, so that no snapshot ever sees it as a
   descendant, is outside the containment and evidence-integrity guarantee. It
   can outlive the run, and Trestle will not know to stop it.
2. **A run started before process identity was recorded (K-19).** A run started
   by a version that recorded no process identity has nothing to recover from.
   After a restart Trestle finalizes it `interrupted`, reports its stop as
   **unconfirmed** (never clean), and sends no signal to any process.

Before you first start a version that records process identity, either stop the
server while no run is live, or check the process table and stop any leftover
worker yourself:

```bash
ps -ax -o pid,command | grep trestle.child.main
```

This is part of the same statement as "not a sandbox" above: plugins run as your
user, and stopping a run is best effort for exactly the processes Trestle can
attribute.

## Operator vs agent

- **Agents** use MCP (`trestle serve`) — can admit and `run` work.
- **Operators** use CLI (`doctor`, `pin`, `unpin`, `recover`) and optional `trestle ops serve` — browse sessions and telemetry; no admit/`run` from the browser.

## Related

- Install and host wiring: [`install.md`](install.md)
- Product overview: [`overview.md`](overview.md)
