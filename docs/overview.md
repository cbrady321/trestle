# What Trestle is

Trestle is a **local execution ledger** with a thin MCP control surface. Coding agents run one-file Python plugins, keep full evidence on disk, and pull only bounded slices into context.

## Why it exists

Agents need to run CLI tools and scripts, and each round trip costs a model turn, tokens, and latency. Driving a multi-stage task one command at a time (run a step, read the output, decide, run the next) multiplies that cost, and raw stdout and logs fill the context window along the way.

Trestle cuts the round trips. A script captures the whole objective (the steps, the branching between them, the retries, and the final check) and executes it in **one call**. The agent states the goal once with `run(plugin="…")` and gets back **one bounded, final, truthful answer** instead of a trail of intermediate output.

Evidence stays on disk, and agents pull small slices only when they need them. That keeps the agent's context free for its own decisions.

## How it works

- The **foundation** (server, wrapper, child runtime, ledger, projection) wraps **scripts** — one Python callable per plugin.
- Plugin authors use **PluginSurface** (`Context`, `@trestle`), and a tree workflow also imports `trestle.workflow` (its declarations, `run_tree` and ports). They do not import Kernel, FastMCP, or ledger internals.
- A large task with many dependent parts runs as **one declared tree**: one `run`, one answer. See [Large tasks: the tree](agents.md#large-tasks-the-tree).
- Agents see **ten frozen MCP tools**. Plugin names are not MCP tools; always `run(plugin="…")`.
- Admission refusals are not runs — they have no `run_id`.
- `fetch` uses opaque handles (`r_…`, `art_…`, `{run_id}/result`), never filesystem paths.
- Default agent transport is **stdio** (process-local). Optional HTTP binds **127.0.0.1 only**.

## Out of scope

- Live tail / websocket log streams
- Per-plugin MCP tools
- Path-based file access from the agent surface
- Remote or multi-user access — localhost / current user only
- Sandbox isolation — plugins run as your user (see [`security.md`](security.md))

## Next

| Doc | Audience |
|-----|----------|
| [`install.md`](install.md) | Install and wire Cursor / Claude |
| [`security.md`](security.md) | Local-only lockdown |
| [`agents.md`](agents.md) | Coding agents — quick start |
| [`plugins.md`](plugins.md) | Write and publish plugins |
| [`packs.md`](packs.md) | Docker / pytest / migration packs |
| [`operator-sessions-telemetry.md`](operator-sessions-telemetry.md) | Human operator UI |
