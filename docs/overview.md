# What Trestle is

Trestle is a **local execution ledger** with a thin MCP control surface. Coding agents run one-file Python plugins, keep full evidence on disk, and pull only bounded slices into context.

## Why it exists

Agents need to run CLI tools and scripts. Naive stdout, logs, and return values flood the context window and make those tools unusable. Trestle contains that flood: evidence stays on disk, and agents retrieve small honest answers when they need them.

## How it works

- The **foundation** (server, wrapper, child runtime, ledger, projection) wraps **scripts** — one Python callable per plugin.
- Plugin authors use **PluginSurface only** (`Context`, `@trestle`). They do not import Kernel, FastMCP, or ledger internals.
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
