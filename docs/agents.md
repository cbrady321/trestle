# Trestle — Agent Guide

**Audience:** coding agents (Cursor, Claude Desktop, other MCP hosts)  
**Install:** [`docs/install.md`](install.md) · **Overview:** [`docs/overview.md`](overview.md) · **Security:** [`docs/security.md`](security.md)  
**Cursor skill:** [`.cursor/skills/trestle/SKILL.md`](../.cursor/skills/trestle/SKILL.md)  
**Full reference:** [`docs/agent-console-mcp.md`](agent-console-mcp.md)  
**Plugins:** [`docs/plugins.md`](plugins.md) · **Packs:** [`docs/packs.md`](packs.md)

---

## What Trestle is

Trestle is a **local execution kernel** with a **fixed MCP surface** (ten tools). You run Python **plugins** via `run`, then pull **bounded evidence** via `query` and `fetch`. Evidence stays on disk; your context window gets small, honest slices.

You do **not** call plugin names as MCP tools. You do **not** use filesystem paths in `fetch`. Admission refusals never include a `run_id` — they are not failed runs.

---

## Before your first `run`

### 1. Install and bootstrap

See [`install.md`](install.md). Short path:

```bash
pip install -e ".[dev,packs]"   # packs optional but recommended for workflow plugins
trestle init                    # creates ~/.trestle, seeds echo.py
trestle doctor                  # health + plugin counts — need plugins ≥ 1
```

An empty catalog means every `run` returns `admission.plugin_not_found`. Check `catalog_hint` on `list_plugins` if the catalog is empty.

**Workflow packs** (Docker / pytest / migrations) require `pip install -e ".[packs]"` and a watched plugin directory — see [`packs.md`](packs.md).

### 2. Attach MCP

Prefer user-level config so every workspace can use Trestle ([`install.md`](install.md)). Repo ships [`.cursor/mcp.json`](../.cursor/mcp.json) as a project override:

```json
{
  "mcpServers": {
    "trestle": {
      "command": "trestle",
      "args": [
        "serve",
        "--plugin-dir",
        "${workspaceFolder}/examples/packs"
      ],
      "env": { "TRESTLE_HOME": "~/.trestle" }
    }
  }
}
```

Use the absolute path to your venv's `trestle` binary if it is not on `PATH`.

**Project plugins** — point the server at repo tool folders:

```bash
trestle serve --plugin-dir ./tools --plugin-dir examples/packs
```

Or persist paths in `$TRESTLE_HOME/config.toml` under `[plugins].paths`.

### 3. Verify

- Host shows **ten** tools (see table below).
- `trestle doctor` → `health: ok`, plugins ≥ 1.
- Optional: `python scripts/smoke_agent_mcp.py` → `SMOKE OK`.

---

## Golden workflow (≤5 turns)

| Step | Tool | What to pass |
|------|------|--------------|
| 1 | `list_plugins` | — discover plugin `name` values |
| 2 | `describe_plugin` | `plugin_id` = plugin name (optional; pull `input_schema` / `return_schema`) |
| 2b | `publish_plugin` | `source` (optional; create/update plugin at runtime) |
| 3 | `run` | `plugin`, `args`, `wait_ms` |
| 4a | `fetch` | `{run_id}/result` + window (success path) |
| 4b | `query` | `view: last_error`, `params: {run_id}` (failure path) |

**Example — success:**

```json
// run
{ "plugin": "echo", "args": { "message": "hello" }, "wait_ms": 5000 }

// fetch — use run_id from RunView
{
  "target": "r_abc123/result",
  "window": { "kind": "jsonpath", "expr": "$.message" }
}
```

**Param naming:** `run` uses `plugin`; `describe_plugin` uses `plugin_id` — same string value.

---

## Ten tools (frozen — do not invent more)

| Tool | Use when |
|------|----------|
| `run` | Create work (or get admission refusal) |
| `await_runs` | Join handles already admitted |
| `cancel` | Request stop of a run |
| `query` | Named view over run history (bounded rows) |
| `fetch` | Bytes of one handle (result, artifact, summary continuation) |
| `pin` / `unpin` | Retention policy |
| `list_plugins` | Catalog names only — no schemas |
| `describe_plugin` | One plugin's schema and metadata |
| `publish_plugin` | Create or update a plugin from Python source |

**Always** `run(plugin="…")`. **Never** call a plugin name as an MCP tool.

---

## Critical rules

1. **Refusals are not runs.** `admission.*` and `publication.*` outcomes have no `run_id`. Do not treat them as failed runs.
2. **Handles, not paths.** `fetch("/tmp/foo")` → `projection.invalid_handle`. Use handles from `RunView` or `query`.
3. **Evidence is post-finalize.** `query(run_tail|last_error|run_events, …)` on a running run → `projection.not_finalized` (`retryable: true`). Wait for terminal state or use `await_runs`.
4. **Check `limits_exceeded`.** When output exceeds caps, `RunView` marks suppressed streams — evidence is honestly incomplete.
5. **Fetch truncation is success.** `truncated: true` with `scan_bytes` means narrow the window and continue — not an error.
6. **Small MCP surface.** Plugin JSON schemas are **not** on `tools/list`. Pull one schema via `describe_plugin` when needed. When-to-pick for `query`/`fetch` lives on MCP resource `trestle://views` (not a second schema dump).

---

## Retrieval — pick the smallest answer

Live catalog: MCP resource **`trestle://views`** (when to pick each named view vs fetch). Plugin schemas stay off that resource.

| Question | Tool | View / target |
|----------|------|---------------|
| What did the plugin return? | `fetch` | `{run_id}/result` + `jsonpath` or `range` |
| Why did it fail? | `query` | `last_error` + `{run_id}` |
| Wrapper stdout lines? | `query` | `run_tail` + `{run_id}` (post-finalize) |
| Run metadata? | `query` | `run` + `{run_id}` |
| Event timeline? | `query` | `run_events` + `{run_id}` |
| Large array in summary? | `fetch` | `RunView.summary.handle` + `jsonpath` / `range` |
| Artifact bytes? | `query` → `fetch` | `run_artifacts` → `art_…` + `head` / `tail` / `grep` |
| Recent failures? | `query` | `recent_failures` |
| Browse runs? | `query` | `recent_runs` (+ `cursor` if `next_cursor`) |

### `run_tail` vs `fetch`

| Evidence | Access |
|----------|--------|
| Wrapper **stdout/stderr** (console pipes) | `query(run_tail)` |
| Plugin **return value** | `fetch({run_id}/result, …)` |
| **Artifact** files | `fetch(art_…, …)` |
| Filesystem paths | **Forbidden** |

Plugins that only use `ctx.log()` may produce **empty** `run_tail` — use `run_events` or the return value instead.

### Named views (`query`)

`run`, `last_error`, `run_tail`, `run_events`, `recent_runs`, `recent_failures`, `run_provenance`, `run_artifacts`, `artifact_refs`

Every `BoundedView` includes `backend`, `as_of`, `items`, `truncated`, `next_cursor`.

### Fetch windows

| Handle | Permitted `window.kind` |
|--------|-------------------------|
| `{run_id}/result` | `jsonpath`, `range`, `head`, `tail`, `grep` |
| `summary.handle` (array continuation) | `jsonpath`, `range` |
| `art_…` (text artifact) | `range`, `head`, `tail`, `grep` |

---

## Wait and long jobs

`run` admits work, starts execution on a background thread, then waits up to `wait_ms`:

- `wait_ms=0` → immediate status frame (may be `queued` or `running`).
- `wait_ms>0` on a slow plugin → returns a **`running` frame** when the deadline elapses; join with `await_runs`.
- Terminal within `wait_ms` → terminal `RunView` in one call.

**Long jobs:** use a short `wait_ms` to get `run_id` quickly, then `await_runs` with a longer `timeout_ms`.

---

## Publishing plugins

See [`plugins.md`](plugins.md) for authoring, filesystem drop-in, and `publish_plugin`.

---

## Common mistakes

| Mistake | Result | Fix |
|---------|--------|-----|
| Plugin name as MCP tool | unknown-tool | `run(plugin=…)` |
| `fetch("/tmp/…")` | `projection.invalid_handle` | Use handle from `RunView` / `query` |
| `query` before finalize | `projection.not_finalized` | `await_runs` or wait for terminal |
| Unknown view name | `projection.invalid_view` | Read `trestle://views`; do not invent names or send SQL |
| Empty catalog | `admission.plugin_not_found` | `trestle init` or `publish_plugin` |
| Bad plugin args | `admission.invalid_args` | `describe_plugin` then retry `run` |
| Pack plugin `valid: false` | `admission.import_failed` | `pip install -e ".[packs]"`; check `catalog_hint` |
| Reused idempotency key, different args | `admission.idempotency_key_conflict` | New key or same args |

---

## Operator vs agent

| Role | Surface | Notes |
|------|---------|-------|
| **Agent** | MCP ten tools (`trestle serve`) | Retrieval-first; bounded slices |
| **Operator (CLI)** | `trestle doctor`, `pin`, `unpin`, `recover` | Retention and diagnostics |
| **Operator (HTTP)** | `trestle ops serve` + console UI | Read-only sessions/telemetry — [`operator-sessions-telemetry.md`](operator-sessions-telemetry.md) |

Agents reach evidence **only** through MCP. Operator HTTP does not expose `run`.

---

## Out of scope

- Per-plugin MCP tools
- Path-based file access
- Live tail / websocket log stream
- Sandbox / remote execution

---

## Further reading

| Doc | Contents |
|-----|----------|
| [`install.md`](install.md) | System-wide install and host wiring |
| [`security.md`](security.md) | Local-only lockdown |
| [`plugins.md`](plugins.md) | Write and publish plugins |
| [`agent-console-mcp.md`](agent-console-mcp.md) | Full playbook — mis-invocation table, smoke checks |
| [`.cursor/skills/trestle/SKILL.md`](../.cursor/skills/trestle/SKILL.md) | Cursor project skill — compact rules |
| [`packs.md`](packs.md) | Workflow packs |
| [`overview.md`](overview.md) | Product overview |
