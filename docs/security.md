# Local-only lockdown

Trestle is a **single-user, localhost** product. External network clients must not be able to reach the MCP or operator surfaces.

## What is locked down

| Surface | Exposure |
|---------|----------|
| `trestle serve` (run by the per-user service) | Binds **`127.0.0.1` only** (default port **18732**). No `--host` flag. Every MCP request must carry the agent token. |
| `trestle ops serve` | Binds **`127.0.0.1` only** (default port **18733**). No `--host` flag. Read-only — does **not** expose `run`. |
| `$TRESTLE_HOME` (default `~/.trestle`) | Per-user ledger, plugins and `agent.token` (`0600`). Recommend `chmod 700 ~/.trestle`. |

CLI rejects `--host` (including `0.0.0.0`). Do not port-forward, tunnel, or reverse-proxy these ports to other machines.

## The agent token

The MCP endpoint is the only way an agent reaches Trestle (there is no stdio transport), so every
agent on the machine connects to `127.0.0.1:18732`. A port on loopback is reachable by **any local
process**, so the endpoint requires a bearer token: `Authorization: Bearer <token>`, where the
token is the contents of `$TRESTLE_HOME/agent.token`, created by `trestle init` (or the server's
first start) with mode `0600`. A request without it, or with another value, is answered 401 before
it reaches any tool. There is no setting that turns the check off.

The token is possession of a file the OS already limits to your user. It bounds the endpoint to "a
process that can read your home" instead of "a process on the machine". It is not a login and does
not make Trestle multi-user. Hosts send it through their MCP config's `headers`, pasted or expanded
from an environment variable ([`install.md`](install.md#2-wire-every-agent-host)). The read-only
ops port takes no token: it admits nothing.

## The service's environment

The per-user service ([`install.md`](install.md)) runs with an environment of exactly `PATH`,
`HOME` and `TRESTLE_HOME`, and that is the environment every plugin starts from. Tokens exported in
your shell profile do not reach it; keep them out of the shell entirely.

## What “local-only” does not mean

- Plugins run **as your user**. They can read/write files, spawn processes, and make network calls the OS allows.
- Trestle **bounds what reaches the agent** (summaries, named views, fetch windows). It is **not** a sandbox that isolates plugins from the host.
- Operator UI and the MCP endpoint are loopback-only; the agent token is not a multi-user login.

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

The old worker was its own session leader, so its plugin's subprocesses share its process
group and killing only the worker's pid leaves them running. Show the group with
`ps -ax -o pid,pgid,command | grep trestle.child.main`, then stop the whole group with
`kill -- -<pgid>`.

This is part of the same statement as "not a sandbox" above: plugins run as your
user, and stopping a run is best effort for exactly the processes Trestle can
attribute.

## Profiles: full and restricted

The operator picks the service profile in `$TRESTLE_HOME/config.toml`; the server reads it once at start, so nothing an agent sends (a tool argument, plugin intent) can change it.

```toml
[profile]
mode = "restricted"        # "full" (default) or "restricted"
allowlist = ["echo"]       # plugin names an agent may run (restricted only)
```

| | `full` (default) | `restricted` |
|---|---|---|
| MCP tools | all ten, unchanged | nine: `publish_plugin` is not registered (not listed, not callable) |
| `run` | any published plugin | only allowlisted plugins; any other is refused `admission.not_allowlisted` before a run id, run directory or process exists |
| `cancel` | any run | only runs received in the same MCP session; another session's run is refused `projection.not_owner` and keeps running |

An unknown `mode` or a malformed `allowlist` stops the server from starting: a config that means to restrict never runs as `full`. A restricted profile with no allowlist admits nothing. The operator's CLI and console (`trestle ops`) are not agent sessions and are not scoped.

Every value an agent can select is a closed set or is validated before it acts: `query` views and `fetch` window kinds are enumerations, `completion` and `await_runs` modes are refused outside their sets, plugin names and handles must resolve, and `args` must match the plugin's schema. No tool argument selects cleanup or removal of a resource it did not create. `unpin` is the one action that is not session-scoped under either profile: it deletes nothing, and garbage collection then applies the operator's retention policy to whatever it unpinned.

**Neither profile is a sandbox.** The restricted profile narrows what an agent may ask Trestle to do; an allowlisted plugin still runs as your user with everything that implies (see “What local-only does not mean” above).

## Operator vs agent

- **Agents** use the service's MCP endpoint — can admit and `run` work. Nothing agent-facing runs `trestle` itself.
- **Operators** start, stop and upgrade the service with `trestle service`.
- **Operators** use CLI (`doctor`, `pin`, `unpin`, `recover`) and optional `trestle ops serve` — browse sessions and telemetry; no admit/`run` from the browser.

## Related

- Install and host wiring: [`install.md`](install.md)
- One per-user service, credentials by name, MCP only (the service, the token and MCP only are implemented; credentials are not yet): [`decisions/service-and-credentials.md`](decisions/service-and-credentials.md)
- Product overview: [`overview.md`](overview.md)
