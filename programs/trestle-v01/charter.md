# Trestle v0.1 — Program Charter

## Guiding light

> Agents should run powerful local CLI tools without flooding the context window. Evidence stays on disk; telemetry is available on pull. Script authors write one Python file and get correct filing, bounds, and projection automatically — without knowing ledger layout, handle grammar, or MCP. Refused requests never look like failed runs. The porch stays thin: nine stable tools, bounded default payloads, handles for everything else.

## Goal

Build **Trestle v0.1** end to end: a durable local execution ledger with a thin MCP control surface (nine fixed tools), spawn-per-run plugin execution, automatic child runtime for script authors, bounded projection for agents, and operator CLI.

## Success definition

An MCP agent can `list_plugins` → `run` a plugin → `await_runs` → `query` history → `fetch` evidence slices — while a script author drops a `.py` file, prints/logs/writes `outputs/`, and never imports Trestle internals. Operator can `trestle doctor` and recover from crash. All of this on macOS and Linux, Python 3.12+, FastMCP 3.4.7 stdio.

## North star (G1)

Minimize tokens and turns per completed task. Every design choice that floods agent context is a failure.

## Product identity

The product is the **Kernel (foundation)**, not FastMCP. FastMCP 3.4.7 is a stdio porch only. Scripts see **PluginSurface only**; they never import Kernel, FastMCP, or ledger internals.

## Entry mode

Brownfield migration: `base_ref=design-frozen` → `target_ref=v0.1-complete`.

## Constraints

- Three Kernel ports: Admit, Execute, Project. Nine MCP tools only.
- Spawn-per-run; wrapper must not import plugin deps.
- Projection law: only Project emits toward MCP; build from `result.index` + `pread`.
- No Tasks mapper, no Docket/Redis, no warm-fork supervisors in v0.1.
- Stage critic gates only; no human blocking unless hopeless escalation.

## Out of capstone

- TTY-class plugin shipping (advertise-and-refuse sufficient)
- M5.1 SQLite (optional)
- FastMCP Tasks / Docket / Redis
- Warm-fork supervisors
- v0.2 composition

## Authority stack

1. `trestle-requirements.md` (Draft v0.7)
2. `hld/hld-interface-architecture-trestle.md` (freeze HLD)
3. `hld/hld-tty-overlay-trestle.md` (additive overlay)
4. `user-stories-from-requirements.md` (US-01–US-24)
5. `spikes/RESULTS.md` (M0 / M0.5 / M0.6 binding decisions)

Code under `trestle/` is provisional scaffolding — replace to match freeze HLD.
