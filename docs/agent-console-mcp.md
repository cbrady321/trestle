# Agent console MCP playbook

**Audience:** coding agents (Cursor, Claude Desktop, other MCP hosts) using `trestle serve`.  
**SSOT for:** nine-tool workflow, console evidence retrieval, host wiring.  
**Kernel freeze:** [`hld/hld-interface-architecture-trestle.md`](../hld/hld-interface-architecture-trestle.md) — do not invent tools or views.  
**Assessment:** [`hld/agent-mcp-usability-assessment.md`](../hld/agent-mcp-usability-assessment.md).

Agents reach run evidence **only** through MCP (`trestle serve`). There is no HTTP BFF. Operators may use `trestle doctor`, `pin`, and `recover` on the CLI today; `query` / `fetch` on the CLI are not shipped in v0.1 — use the MCP tools below.

---

## 0. Attach and bootstrap

### Install

```bash
pip install -e ".[dev]"
pytest -q   # optional sanity check
```

### Plugin home

Trestle loads plugins from `$TRESTLE_HOME/plugins/` (default `~/.trestle/plugins/`). **An empty plugin directory means every `run` returns `admission.plugin_not_found`.**

```bash
mkdir -p ~/.trestle/plugins
cp examples/plugins/echo.py ~/.trestle/plugins/
trestle doctor   # plugins: 1
```

Copy any one-file plugin into that directory. Hot reload: drop a new `.py` file; call `list_plugins` again (`registry_version` bumps).

### Host wiring

**Cursor** — repo ships [`.cursor/mcp.json`](../.cursor/mcp.json). After `pip install -e .`, enable the `trestle` server in Cursor MCP settings (or merge the JSON into your user config).

**Claude Desktop** — same shape under `mcpServers` in your desktop config file.

```json
{
  "mcpServers": {
    "trestle": {
      "command": "trestle",
      "args": ["serve"],
      "env": {
        "TRESTLE_HOME": "~/.trestle"
      }
    }
  }
}
```

Use the absolute path to the Python environment where `trestle` is installed if `trestle` is not on the host `PATH` (e.g. `"command": "/path/to/venv/bin/trestle"`).

### Verify attach

1. `trestle doctor` — `health: ok`, `plugins` ≥ 1 after bootstrap.
2. Host shows **nine** MCP tools: `run`, `await_runs`, `cancel`, `query`, `fetch`, `pin`, `unpin`, `list_plugins`, `describe_plugin`.
3. `tools/list` payload is small (~2.4 KB) — plugin schemas are **not** inlined (G1).

FastMCP prints a startup banner on stderr; hosts should ignore it.

---

## 1. Golden workflow (≤5 turns)

Typical successful path:

| Turn | Tool | Purpose |
|------|------|---------|
| 1 | `list_plugins` | Discover plugin `name` values |
| 2 | `describe_plugin` | Optional — `input_schema` when args are unknown (`plugin_id` = plugin name) |
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

## 2. Nine tools (frozen)

| Tool | Use when |
|------|----------|
| `run` | Create work (or get admission refusal) |
| `await_runs` | Join handles already admitted elsewhere |
| `cancel` | Request stop of a run |
| `query` | Named view over run history (bounded rows) |
| `fetch` | Bytes of one handle (result, artifact, summary continuation) |
| `pin` / `unpin` | Retention policy |
| `list_plugins` | Catalog names only — no schemas |
| `describe_plugin` | One plugin's schema and metadata |

Do **not** call a plugin name as an MCP tool. Always `run(plugin="…")`.

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

`admission.*` outcomes (`plugin_not_found`, `invalid_args`, `queue_full`, …) **never** include `run_id`. Do not treat them as runs.

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
| `query` with unknown view | `projection.invalid_view` | Use a view from §5 |
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
| **Agent** | MCP nine tools | Retrieval-first; bounded slices |
| **Operator** | `trestle doctor`, `pin`, `unpin`, `recover` | Same admission rules; richer diagnostics |

Human **sessions / telemetry** UI is future work — not in v0.1.

---

## 10. Out of scope

- Tenth MCP tool or HTTP adapter
- Live tail / websocket log stream
- Per-plugin MCP tools
- Path-based file access
- Sandbox / remote execution

Escalate kernel gaps via [`hld/plan-daytona-clean-room-scope.md`](../hld/plan-daytona-clean-room-scope.md) — freeze amendment is last resort.
