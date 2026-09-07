# Agent console MCP playbook

**Audience:** coding agents (Cursor, Claude Desktop, other MCP hosts) using `trestle serve`.  
**Quick start:** [`docs/agents.md`](agents.md) — scannable agent guide. **Cursor skill:** [`.cursor/skills/trestle/SKILL.md`](../.cursor/skills/trestle/SKILL.md).  
**Install / lockdown:** [`install.md`](install.md) · [`security.md`](security.md).  
**SSOT for:** ten-tool workflow, console evidence retrieval, host wiring. Do not invent tools or views.

Agents reach run evidence **only** through MCP (`trestle serve`). There is no HTTP BFF for agents. Operators use `trestle doctor`, `pin`, and `recover` on the CLI; bounded retrieval for humans is via **`trestle ops serve`** ([`operator-sessions-telemetry.md`](operator-sessions-telemetry.md)). `query` / `fetch` are not CLI subcommands in v0.1 — agents use MCP tools below.

---

## 0. Attach and bootstrap

### Install

```bash
pip install -e ".[dev]"
pip install -e ".[packs]"          # workflow packs: Docker, pytest, migrations
pytest -q   # optional sanity check
```

Workflow pack plugins live in `examples/packs/`. See [`docs/packs.md`](packs.md).

### Plugin home

Trestle loads plugins from configured **plugin search paths** (default `$TRESTLE_HOME/plugins/`). **An empty catalog means every `run` returns `admission.plugin_not_found`.**

```bash
trestle init                    # create ~/.trestle, plugins/, seed echo.py
trestle doctor                  # plugin_search_paths + plugin counts
```

Or manually:

```bash
mkdir -p ~/.trestle/plugins
cp examples/plugins/echo.py ~/.trestle/plugins/
trestle doctor
```

**Project repos** — point the server at repo tool folders (repeatable flag replaces config/env for that process):

```bash
trestle serve --plugin-dir ./tools --plugin-dir ./packages/scripts
```

Or persist in `$TRESTLE_HOME/config.toml`:

```toml
[plugins]
paths = ["~/.trestle/plugins", "/abs/path/to/repo/tools"]
```

Or env: `TRESTLE_PLUGIN_DIRS="$HOME/.trestle/plugins:/abs/repo/tools"`.

Hot reload: drop a new `.py` file into any watched path, or call `publish_plugin`; then `list_plugins` (`registry_version` bumps).

### Host wiring

Full Cursor / Claude Desktop / Claude Code paths: [`install.md`](install.md). Lockdown: [`security.md`](security.md).

**Cursor** — repo ships [`.cursor/mcp.json`](../.cursor/mcp.json). After `pip install -e .`, enable the `trestle` server in Cursor MCP settings (or merge into `~/.cursor/mcp.json`).

**Claude Desktop** — same shape under `mcpServers` in the desktop config file.

**Optional streamable HTTP** (alongside stdio, not a replacement; loopback only):

```bash
trestle serve --transport streamable-http --port 18732
# endpoint: http://127.0.0.1:18732/mcp
```

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
      "env": {
        "TRESTLE_HOME": "~/.trestle"
      }
    }
  }
}
```

This repo ships [`.cursor/mcp.json`](../.cursor/mcp.json) with `examples/packs` wired. For generic bootstrap (echo only), use `"args": ["serve"]`.

Use the absolute path to the Python environment where `trestle` is installed if `trestle` is not on the host `PATH` (e.g. `"command": "/path/to/venv/bin/trestle"`).

### Verify attach

1. `trestle doctor` — `health: ok`, `plugins` ≥ 1 after bootstrap.
2. Host shows **ten** MCP tools: `run`, `await_runs`, `cancel`, `query`, `fetch`, `pin`, `unpin`, `list_plugins`, `describe_plugin`, `publish_plugin`.
3. `tools/list` payload is small (~2.4 KB) — plugin schemas are **not** inlined.

FastMCP prints a startup banner on stderr; hosts should ignore it.

---

## 1. Golden workflow (≤5 turns)

Typical successful path:

| Turn | Tool | Purpose |
|------|------|---------|
| 1 | `list_plugins` | Discover plugin `name` values |
| 2 | `describe_plugin` | Optional — `input_schema` / `return_schema` when args or result shape are unknown (`plugin_id` = plugin name) |
| 2b | `publish_plugin` | Optional — create or update a plugin from Python `source` at runtime |
| 3 | `run` | `plugin`, `args`, `wait_ms` — see §3 for blocking behavior |
| 4a | `fetch` | Success — read return value from `{run_id}/result` |
| 4b | `query` | Failure — `view: last_error`, `params: {run_id}` |

**Example (success):**

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

## 2. Ten tools (frozen MCP surface)

| Tool | Use when |
|------|----------|
| `run` | Create work (or get admission refusal) |
| `await_runs` | Join handles already admitted elsewhere |
| `cancel` | Request stop of a run |
| `query` | Named view over run history (bounded rows) |
| `fetch` | Bytes of one handle (result, artifact, summary continuation) |
| `pin` / `unpin` | Retention policy |
| `list_plugins` | Catalog names only — no schemas; includes `plugin_search_paths`; `catalog_hint` when empty |
| `describe_plugin` | One plugin's schema and metadata |
| `publish_plugin` | Create or update a plugin from Python source at runtime |

Do **not** call a plugin name as an MCP tool. Always `run(plugin="…")`.

### `publish_plugin` (runtime catalog updates)

Publish or update a plugin without restarting the server. Writes source to the primary plugin directory, validates, snapshots, and bumps `registry_version`.

```json
{
  "source": "from trestle.plugin.surface import Context, trestle\n\n@trestle\ndef my_tool(ctx: Context) -> dict[str, str]:\n    return {\"ok\": \"yes\"}\n",
  "name": null
}
```

| Field | Required | Notes |
|-------|----------|-------|
| `source` | yes | Full Python source; must contain one `@trestle` entry point |
| `name` | no | If set, must match the `@trestle` function name |

**Success** → `PublishView`: `name`, `snapshot_id`, `registry_version`, `source_sha256`, `created` (true if new file).

**Failure** → `RequestOutcome` with `origin: "publication"` (no `run_id`):

| Code | Meaning |
|------|---------|
| `publication.invalid_source` | Python syntax error |
| `publication.no_entrypoint` | No `@trestle` function |
| `publication.name_mismatch` | `name` param ≠ entry point |
| `publication.source_too_large` | Source &gt; 512 KB |
| `publication.validation_failed` | Snapshot failed; previous version kept if any |

After publish: `list_plugins` → optional `describe_plugin` → `run(plugin=…)`.

Filesystem drop-in (copy to `plugins/`) still works and uses the same hot-reload pipeline.

---

## 3. Wait, blocking, and honesty

### `run` honors `wait_ms` (R-WAIT-1, R-WAIT-14)

`ControlSurface.run` admits, starts `Conductor.drive` on a **background thread**, then waits up to `wait_ms` via `Project.await_one`.

- `wait_ms=0` → immediate status frame (may be `queued` or `running`).
- `wait_ms>0` on a slow plugin → returns a **`running` frame** when the deadline elapses; join with `await_runs`.
- Terminal within `wait_ms` → terminal `RunView` in one call (same as before).

Long jobs: use a short `wait_ms` to get `run_id` quickly, then `await_runs` with a longer `timeout_ms` — or set a large `wait_ms` if the host allows a long stdio `tools/call`.

### Evidence finalization (R-QB-28)

While a run is still executing, evidence-stream views (`run_tail`, `run_events`, `last_error`, etc.) return:

```json
{
  "code": "projection.not_finalized",
  "message": "...",
  "retryable": true,
  "origin": "projection"
}
```

**Safe next:** `await_runs` with the `run_id`, or wait until `run` returns terminal, then query again. **No live tail** of a running run.

After MCP `run` returns, evidence is finalized for that run — `not_finalized` is uncommon in single-session flows.

### Admission refusals

`admission.*` outcomes (`plugin_not_found`, `invalid_args`, `queue_full`, …) **never** include `run_id`. Do not treat them as runs. `invalid_args` means the plugin schema (from `describe_plugin`) did not match `run.args` — pull that schema and retry.

### `limits_exceeded`

When wrapper or child output exceeds caps, the terminal `RunView` includes `limits_exceeded` markers (stream, limit, bytes suppressed). Evidence is **honestly incomplete** — check this before assuming full logs.

### Fetch scan budget

When `fetch` hits scan limits, the response is **success** with `truncated: true` and `scan_bytes` set — not `isError`. Narrow the window and continue.

---

## 4. Retrieval decision tree

Ask one question; pick one path. Prefer the **smallest** answer.

| Question | Tool | View / target / window |
|----------|------|------------------------|
| What did the plugin return? | `fetch` | `{run_id}/result` + `jsonpath` or `range` |
| Why did it fail? | `query` | `last_error` + `{run_id}` |
| What printed to wrapper stdout? | `query` | `run_tail` + `{run_id}` (post-finalize only) |
| Structured run metadata? | `query` | `run` + `{run_id}` |
| Event timeline? | `query` | `run_events` + `{run_id}` |
| Large array in summary? | `fetch` | `RunView.summary.handle` + `jsonpath` / `range` |
| Artifact file bytes? | `query` → `fetch` | `run_artifacts` → `art_…` + `head` / `tail` / `grep` |
| Pattern in artifact or result text? | `fetch` | `art_…` or `{run_id}/result` + `grep` |
| TTY-class captured output? | `fetch` | `art_…` — **not** `run_tail` |
| Recent failures? | `query` | `recent_failures` |
| Browse runs? | `query` | `recent_runs` (+ `cursor` if `next_cursor`) |
| Data suppressed? | inspect `RunView` | `limits_exceeded` |

### `run_tail` vs `fetch`

| Evidence | Access |
|----------|--------|
| Wrapper **stdout/stderr lines** (console pipes) | `query(run_tail)` — bounded rows |
| Plugin **return value** | `fetch({run_id}/result, …)` |
| **Artifact** files | `fetch(art_…, …)` |
| Path like `/tmp/foo` or `{run_id}/console/stdout` | **Forbidden** — `projection.invalid_handle` |

Plugins that only use `ctx.log()` may produce **empty** `run_tail` — use `run_events` or the return value instead.

---

## 5. Named views (`query`)

When to pick each view vs `fetch`: MCP resource **`trestle://views`**. That catalog is not a plugin-schema dump.

| View | Params | Rows |
|------|--------|------|
| `run` | `run_id` | Status, plugin, state, timing |
| `last_error` | `run_id` | Last error event (empty if none) |
| `run_tail` | `run_id` | Wrapper stdout lines, oldest first |
| `run_events` | `run_id` | Structured events |
| `recent_runs` | — | Recent runs (paginated) |
| `recent_failures` | — | Recent failed runs |
| `run_provenance` | `run_id` | Snapshot, hashes |
| `run_artifacts` | `run_id` | Artifact ids for the run |
| `artifact_refs` | `artifact_id` | Producer / referrers |

Every `BoundedView` includes `backend`, `as_of`, `items`, `truncated`, `next_cursor`.

---

## 6. Fetch windows

Handles are opaque — never filesystem paths.

| Handle source | Permitted `window.kind` |
|---------------|-------------------------|
| `{run_id}/result` | `jsonpath`, `range`, `head`, `tail`, `grep` |
| `summary.handle` (array continuation) | `jsonpath`, `range` |
| `art_…` (text artifact) | `range`, `head`, `tail`, `grep` |

Wrong kind → `projection.invalid_args`. Path-shaped target → `projection.invalid_handle`.

**Examples:**

```json
{ "target": "r_abc/result", "window": { "kind": "jsonpath", "expr": "$.message" } }
{ "target": "art_xyz", "window": { "kind": "tail", "count": 20 } }
{ "target": "art_xyz", "window": { "kind": "grep", "pattern": "ERROR" } }
```

---

## 7. Mis-invocation recovery

| Mistake | Result | Safe next |
|---------|--------|-----------|
| `fetch("/tmp/…")` | `projection.invalid_handle` | Use handle from `RunView` / `query` |
| `fetch(summary.handle, {kind: "tail"})` | `projection.invalid_args` | Use `jsonpath` or `range` on array handle |
| `query` with unknown view | `projection.invalid_view` | Read `trestle://views`; do not invent names or send SQL |
| `query(run_tail)` before finalize | `projection.not_finalized` (`retryable`) | Wait for terminal; `await_runs` |
| `await_runs` with one bad id | Entire call fails — no partial batch | Fix ids and retry |
| `run` while draining | `admission.service_draining` (no `run_id`) | Stop or wait |
| Plugin name as MCP tool | Protocol unknown-tool | `run(plugin=…)` |
| Reused idempotency key, different args | `admission.idempotency_key_conflict` | New key or same args |

---

## 8. Smoke verification

Run after install and plugin bootstrap:

```bash
pip install -e ".[dev]"
python scripts/smoke_agent_mcp.py
```

Expected: `SMOKE OK` with a sample `run_id`, `fetch` jsonpath hit, and refusal checks.

Manual MCP check: start `trestle serve`, call `list_plugins` → `run(echo)` → `fetch({run_id}/result, jsonpath $.message)`.

---

## 9. Operator vs agent

| Role | Surface | Notes |
|------|---------|-------|
| **Agent** | MCP ten tools (`trestle serve`) | Retrieval-first; bounded slices |
| **Operator (CLI)** | `trestle doctor`, `pin`, `unpin`, `recover` | Retention and diagnostics |
| **Operator (HTTP)** | `trestle ops serve` + `console/web/` | Read-only sessions/telemetry — see [`operator-sessions-telemetry.md`](operator-sessions-telemetry.md) |

Agents reach evidence **only** through MCP. Operator HTTP mirrors the same `query` / `fetch` admission rules — it does not replace MCP or expose `run` from the browser.

---

## 10. Out of scope

- Eleventh MCP tool or agent HTTP BFF
- Live tail / websocket log stream
- Per-plugin MCP tools
- Path-based file access
- Sandbox / remote execution
- Binding HTTP off `127.0.0.1`
