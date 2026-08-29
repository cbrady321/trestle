---
name: trestle
description: >-
  Run local Python plugins and retrieve bounded run evidence via Trestle MCP
  (trestle serve). Use when calling run, query, fetch, list_plugins,
  describe_plugin, publish_plugin, awaiting runs, or diagnosing admission
  refusals and projection errors.
---

# Trestle

## SSOT

Read [`docs/agents.md`](../../../docs/agents.md) first. Full playbook: [`docs/agent-console-mcp.md`](../../../docs/agent-console-mcp.md).

## Quick rules

1. **Ten frozen MCP tools only** — never invent tools or call plugin names as MCP tools.
2. **Always** `run(plugin="name", args={…}, wait_ms=…)` to execute work.
3. **Refusals are not runs** — `admission.*` / `publication.*` outcomes have no `run_id`.
4. **Handles, not paths** — `fetch` targets are opaque handles (`r_…`, `art_…`, `{run_id}/result`).
5. **Pull schemas** — `list_plugins` has names only; use `describe_plugin(plugin_id=…)` for `input_schema`.
6. **Smallest retrieval** — return value → `fetch({run_id}/result)`; failure → `query(last_error)`; stdout → `query(run_tail)`.

## Golden workflow

```
list_plugins → describe_plugin? → run → fetch(result) | query(last_error)
```

Long jobs: short `wait_ms` for `run_id`, then `await_runs(timeout_ms=…)`.

## Bootstrap (if catalog empty)

```bash
trestle init && trestle doctor
```

Or `publish_plugin` with `@trestle` Python source. Empty catalog → `admission.plugin_not_found`.

## Plugin authoring

```python
from trestle.plugin.surface import Context, trestle

@trestle
def my_tool(ctx: Context, arg: str) -> dict:
    ctx.log("working")
    return {"result": arg}
```

PluginSurface only — no Kernel, FastMCP, or ledger imports.

## When stuck

| Symptom | Next step |
|---------|-----------|
| `plugin_not_found` | `list_plugins`; check `catalog_hint`; `trestle init` or `publish_plugin` |
| `import_failed` | Plugin deps missing — check `catalog_hint` (pack plugins need `pip install -e ".[packs]"`) |
| `invalid_handle` | Use handle from `RunView` / `query`, not a filesystem path |
| `not_finalized` | `await_runs` or wait for terminal `run` |
| `invalid_view` | Use: `run`, `last_error`, `run_tail`, `run_events`, `recent_runs`, `recent_failures`, `run_provenance`, `run_artifacts`, `artifact_refs` |
| Empty `run_tail` | Plugin used `ctx.log` not print — try `run_events` or `fetch({run_id}/result)` |
| `limits_exceeded` on `RunView` | Evidence truncated — fetch narrower windows |

## Verify

```bash
python scripts/smoke_agent_mcp.py   # expect SMOKE OK
python scripts/smoke_packs.py       # expect PACKS SMOKE OK (requires pip install -e ".[packs]")
```

## Workflow packs

Pre-built plugins for Docker stacks, pytest, and migrations. Install `pip install -e ".[packs]"`, then `trestle serve --plugin-dir examples/packs`. Guide: [`docs/packs.md`](../../../docs/packs.md).

| Symptom | Next step |
|---------|-----------|
| Pack plugins `valid: false` | Check `catalog_hint` on `list_plugins`; install `.[packs]` |
| `admission.import_failed` | Missing `trestle_packs` — see catalog_hint install line |
