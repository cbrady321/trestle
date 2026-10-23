---
name: trestle
description: >-
  Run local Python plugins and retrieve bounded run evidence via the Trestle
  service's MCP endpoint. Use when calling run, query, fetch, list_plugins,
  describe_plugin, publish_plugin, awaiting runs, or diagnosing admission
  refusals and projection errors.
---

# Trestle

## SSOT

Read [`docs/agents.md`](../../../docs/agents.md) first. The full agent skill (one call per objective, chaining with `after=` and idempotency keys, `blocked` runs, unattended plugins) is [`skills/trestle/SKILL.md`](../../../skills/trestle/SKILL.md). Full playbook: [`docs/agent-console-mcp.md`](../../../docs/agent-console-mcp.md). Install: [`docs/install.md`](../../../docs/install.md). Plugins: [`docs/plugins.md`](../../../docs/plugins.md).

## Quick rules

1. **Ten frozen MCP tools only** — never invent tools or call plugin names as MCP tools.
2. **Always** `run(plugin="name", args={…}, wait_ms=…)` to execute work; for a long job add `completion="terminal"` (one call, finished run).
3. **Refusals are not runs** — `admission.*` / `publication.*` outcomes have no `run_id`.
4. **Handles, not paths** — `fetch` targets are opaque handles (`r_…`, `art_…`, `{run_id}/result`).
5. **Pull schemas** — `list_plugins` has names only; use `describe_plugin(plugin_id=…)` for `input_schema`.
6. **View catalog** — read MCP resource `trestle://views` for when to pick each `query` view vs `fetch` (not plugin schemas).
7. **Smallest retrieval** — return value → `fetch({run_id}/result)`; failure → `query(last_error)`; stdout → `query(run_tail)`.
8. **MCP only**: never run `trestle`, or the work a plugin does, in your own shell. If the
   tools are missing or the service does not answer, say so and stop; the service is the
   operator's. A message naming what "the operator runs" is for the operator: report it.
   Never run it yourself, through `exec` or any other way.
9. **Ask the service** — `query(view="status")` (no params) gives the limits (deadline ceiling, longest key ttl) and how busy it is (free slots, live runs, environments in use and who holds them).
10. **One-off commands** — `run(plugin="exec", args={"argv": [...], "cwd": "<abs>"})` when `list_plugins` has `exec` (and, under the restricted profile, `allowlisted: true`); no shell is added.

## Golden workflow

```
list_plugins → describe_plugin? → run → fetch(result) | query(last_error)
```

Need a view name? Read MCP resource `trestle://views` first (not plugin schemas).

Long jobs: pass `completion="terminal"` (with `wait_ms` above zero) so one `run` call returns only a finished run, bounded by the run's deadline plus a margin. `await_runs` is for bounded mode only (the default), to join a `running` frame.

## Large tasks (trees)

- A job with many dependent parts: run a registered tree plugin once (`completion="terminal"`); pass its environment argument explicitly.
- Read `answer.outcome`, then `answer.primary` (`path`, `code`, `human_action`, `resend`), then `answer.listed`. Not `state` or `outcome.class`: they read `succeeded` / `passed` whenever the plugin returned normally.
- Full answer: `fetch("<run_id>/answer", {"kind": "head", "count": 1})`. Stop: `cancel` the root run id.
- Ad-hoc work: `run(wait_ms=0)` per registered plugin, then `await_runs(mode="all")`. Do not `publish_plugin` a one-off tree.
- Details: [`docs/agents.md` § Large tasks: the tree](../../../docs/agents.md#large-tasks-the-tree).

## Bootstrap (if catalog empty)

An empty catalog carries a catalog_hint on list_plugins: report it to the operator, or publish_plugin under the full profile. Empty catalog → `admission.plugin_not_found`.

## Plugin authoring

```python
from trestle.plugin.surface import Context, trestle

@trestle
def my_tool(ctx: Context, arg: str) -> dict[str, str]:
    ctx.log("working")
    return {"result": arg}
```

Return a parameterized type (`dict[str, str]`, not bare `dict`): publication refuses an unparameterized generic.
PluginSurface, plus `trestle.workflow` for a tree workflow — no Kernel, FastMCP, or ledger imports. Call form for metadata:
`@trestle(deadline=..., packages=[...])` (literals only; `async def` entries are refused). A declared
`deadline` above 300 s is honoured and shown by `describe_plugin` (`deadline_s`, `deadline_source`);
above the ceiling it is refused with `admission.budget_does_not_fit`. Declared `packages` are
recorded, not snapshotted: editing one after admission stops the run with
`execution.provenance_mismatch`.

## When stuck

| Symptom | Next step |
|---------|-----------|
| `plugin_not_found` | `list_plugins`; check `catalog_hint`; report it to the operator, or `publish_plugin` |
| `invalid_args` | `describe_plugin(plugin_id=…)` then retry `run` with matching args |
| `import_failed` | Plugin deps missing — check `catalog_hint`; report it: the operator installs the packs |
| `environment_busy` | send yours with `after={"run": held_by, "when": "ended"}`; cancel `held_by` only if it blocks you and `query(view="run")` shows `session_ended: true` |
| any `retryable: true` refusal | wait `retry_after_s`, then resend; again and again: report it |
| `not_allowlisted` | `list_plugins` marks `allowlisted`; use one that is, or report it |
| `not_owner` | another session's run, and that session is still open: leave it |
| `invalid_handle` | Use handle from `RunView` / `query`, not a filesystem path |
| `not_finalized` | `await_runs` or wait for terminal `run` |
| `invalid_view` | Read `trestle://views`; do not invent names or send SQL |
| Empty `run_tail` | Plugin used `ctx.log` not print — try `run_events` or `fetch({run_id}/result)` |
| `limits_exceeded` on `RunView` | Evidence truncated — fetch narrower windows |

## Workflow packs

Pre-built plugins for Docker stacks, pytest, and migrations. They are bundled packs: the operator runs `trestle pack install stack` (no restart). Guide: [`docs/packs.md`](../../../docs/packs.md).

| Symptom | Next step |
|---------|-----------|
| Pack plugins `valid: false` | Check `catalog_hint` on `list_plugins`; report it: the operator installs the packs |
| `admission.import_failed` | A pack's library is missing: report the `catalog_hint` (the operator's `trestle pack install <pack>`) |
