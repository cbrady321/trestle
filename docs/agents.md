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
| 3 | `run` | `plugin`, `args`, `wait_ms`, optional `completion` (`"bounded"` default, or `"terminal"`) |
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

**Dates in `args`:** a datetime must carry a timezone (`2026-01-01T00:00:00Z`); a naive or malformed date or datetime is refused with `admission.invalid_args` and no run id. Supported argument types: [`plugins.md`](plugins.md#supported-types).

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

## Restricted profile

An operator may run Trestle with `[profile] mode = "restricted"` ([`security.md`](security.md)). Then the host lists **nine** tools (no `publish_plugin`), `run` accepts only the allowlisted plugins, and `cancel` works only on runs this MCP session started.

| Refusal | Meaning | Fix |
|---------|---------|-----|
| `admission.not_allowlisted` | Plugin is not on the operator's allowlist (no `run_id`, nothing spawned) | Run an allowlisted plugin (ask the operator which); arguments cannot widen the list |
| `projection.not_owner` | `cancel` for a run another session started | Cancel only your own runs; the run continues |

The full profile (default) is unchanged: ten tools, any published plugin, any run cancellable.

---

## Critical rules

1. **Refusals are not runs.** `admission.*` and `publication.*` outcomes have no `run_id`. Do not treat them as failed runs.
2. **Handles, not paths.** `fetch("/tmp/foo")` → `projection.invalid_handle`. Use handles from `RunView` or `query`.
3. **Evidence is post-finalize.** `query(run_tail|last_error|run_events, …)` on a running run → `projection.not_finalized` (`retryable: true`). Wait for terminal state or use `await_runs`.
4. **A run that did not succeed explains itself (K-10).** `RunView.error` is `{code, phase, message}`: `code` is one of the stable `execution.*` codes (`import_failed`, `bind_failed`, `plugin_raised`, `result_unencodable`, `provenance_mismatch`, `cancelled`, `deadline_exceeded`, `worker_exit`, `interrupted`), `phase` says where it happened, and `message` is bounded (512 bytes) with host paths replaced by `<home>`, `<run>`, `<cwd>` and `<user-home>`. `query(last_error)` returns the same message; before this it said only `failed`, and `RunView.error` was never set. Both read one ledger record, `error_record`, so they agree, and they keep agreeing after a restart. Older runs, recorded before the record existed, keep the old `last_error` text and no `error`.
5. **Check `limits_exceeded`.** When output exceeds caps, `RunView` marks suppressed streams — evidence is honestly incomplete. There is one marker per (stream, limit), however many events or artifacts it dropped; its `bytes_suppressed` is the total. Artifact caps (`stream: "artifacts"`, `max_artifact_count`, `max_artifact_bytes`) mark artifacts that were not stored.
6. **Fetch truncation is success.** `truncated: true` with `scan_bytes` means narrow the window and continue — not an error. On `query`, `truncated: true` with `next_cursor` set means follow the cursor. With `next_cursor` null the recency window (500 runs) or the scan budget left runs out: reaching past it is not offered, and a run-scoped view of such a run by id answers `projection.outside_window` (an id with no run stays `projection.invalid_handle`).
7. **Small MCP surface.** Plugin JSON schemas are **not** on `tools/list`. Pull one schema via `describe_plugin` when needed. When-to-pick for `query`/`fetch` lives on MCP resource `trestle://views` (not a second schema dump).

---

## Retrieval — pick the smallest answer

Live catalog: MCP resource **`trestle://views`** (when to pick each named view vs fetch). Plugin schemas stay off that resource.

| Question | Tool | View / target |
|----------|------|---------------|
| What did the plugin return? | `fetch` | `{run_id}/result` + `jsonpath` or `range` |
| Why did it fail? | `RunView.error`, or `query` | `last_error` + `{run_id}` |
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

## Run states

A run is always in exactly one of these states. The same vocabulary is in
[`agent-console-mcp.md`](agent-console-mcp.md) §3. Each state names the code
that produces it; a state with no producer is **reserved** and is never written.

<!-- K-13 -->
| State | Kind | Producer |
|-------|------|----------|
| `queued` | projected | Admission writes the `created` and `admitted` ledger rows; the run is `queued` until the `started` row exists. |
| `running` | projected | The conductor writes `started` when the worker begins; the run is `running` until evidence is finalized. |
| `succeeded` | terminal | The conductor, when the worker exits 0. |
| `failed` | terminal | The conductor, when the worker exits 1 (the plugin raised) or reports nothing usable. |
| `cancelled` | terminal | The conductor, when it observes the run's cancel request. |
| `timed_out` | terminal | The wrapper and conductor, when the run outlives its deadline. |
| `worker_exit` | terminal | The wrapper, when the worker exits with a code other than 0 or 1 (for example 3). |
| `interrupted` | terminal | Recovery, at the next start, for a run whose ledger has no terminal row. |
| `crashed` | terminal | **Reserved.** It stays in the ledger's terminal-kind set so old readers keep working, but no code path writes it. |
<!-- /K-13 -->

`crashed` is reserved (K-13), not a state a run can reach. A run whose worker dies
abnormally is `worker_exit`; a run whose server died is `interrupted`.

---

## Wait and long jobs

`run` admits work, starts execution on a background thread, then waits up to `wait_ms`:

- `wait_ms=0` → immediate status frame (may be `queued` or `running`).
- `wait_ms>0` on a slow plugin → returns a **`running` frame** when the deadline elapses; join with `await_runs`.
- Terminal within `wait_ms` → terminal `RunView` in one call.

**Long jobs:** use a short `wait_ms` to get `run_id` quickly, then `await_runs` with a longer `timeout_ms`.

**`completion` (optional, default `"bounded"`, which is everything above unchanged).** Pass `completion="terminal"` to make one `run` call return only a finished run: the response follows the run's finalized terminal row (evidence finalized, then `succeeded`, `failed`, `cancelled`, `timed_out` or `interrupted`), never a `running` frame. The call is bounded by the run's own deadline plus a published finalization margin, not by `wait_ms`: a `wait_ms` above zero is accepted and ignored, and `wait_ms=0` (or below) is refused `admission.invalid_args` because a terminal call has to wait; so is any `completion` value other than `bounded` or `terminal`. If the run is somehow not terminal by that bound, the call answers `projection.terminal_wait_exceeded` (a refusal, not a run state; the run itself is unaffected and `await_runs` still joins it). While a `terminal` call is held, other calls (`cancel`, `query`, `await_runs`) are still answered.

**One class per finished run.** A finished `RunView` carries `outcome: {class, code, identity, recovered}`. `class` is one of `passed`, `cancelled`, `timed_out`, `execution_error` (a plain plugin reaches these four; `failed` and `blocked` are reserved for workflow results). `code` is the `execution.*` code for an `execution_error` and `null` for the others; `state` keeps its meaning underneath.

<!-- K-8 -->
### A succeeded run leaves no process behind (K-8)

When a run ends, Trestle stops the processes it can attribute to the run (its recorded descendants, see the containment boundaries in `docs/security.md`), and that includes a run that **succeeded**: a plugin that returns normally while a child, or a grandchild in its own session, is still running has that process stopped (SIGTERM first, SIGKILL after the grace period) before the run's evidence is finalized. Nothing the run started writes into `evidence/` or changes `result.json` after the terminal answer. The run's class and summary are unchanged (a succeeded run stays `succeeded`), and `cleanup.processes` reads `released` once the stop is confirmed, `unknown` when it could not be.

This is a knowing change (K-8): before it, a process a plugin forgot outlived a succeeded run. It is on by default (`REAP_ON_SUCCESS` in `trestle/server/conductor.py`), the same kill every other way a run can end has always received.
<!-- /K-8 -->

---

## Snapshot identity

<!-- K-2 -->
A plugin's snapshot id (`snap_` plus 16 hex digits) no longer hashes `plugin.py` alone. It covers the
source, the digests of the packages the plugin declares (`@trestle(packages=[...])`), the input and
return schemas, the declared metadata (deadline, summary fields, packages, env arg, secrets and the
summary budget) and the Trestle runtime version. Two publications with equal ids ran equal code
and declarations; an edit to a declared package or a declared value gives a new id. Anyone who
compared snapshot ids to detect a change in `plugin.py` bytes should compare `source_sha256`, which
is still the hash of that file. A schema file is never rewritten under an existing id. A run keeps
the id it was admitted under, and stops with `execution.provenance_mismatch` if a declared package
changed after admission (the packages are recorded, not snapshotted; see `docs/plugins.md`).
<!-- /K-2 -->

## Cancel, deadlines and cleanup

A run has one deadline, fixed when it is admitted: the plugin's declared deadline (300 s when it
declares none), counted from admission, so time spent queued or held counts against it.
<!-- K-9 -->
A plugin may declare a longer deadline in its `@trestle(deadline=...)` call form (one that declares
none keeps 300 s); `describe_plugin` reports it as `deadline_s` with `deadline_source` (`declared` or `default`), and it is enforced, not
advisory. A declared deadline above the ceiling (3600 s) is refused before any run id with
`admission.budget_does_not_fit`.
<!-- /K-9 --> `cancel` and the deadline are the two ways a run is stopped, and
both stop the **whole process tree**, not just the plugin's own process. The supervisor sends
SIGTERM to every process attributable to the run at once, waits at most `grace`, sends SIGKILL,
and waits at most `kill` for confirmation. Attributable means the run's process group, and every
descendant by parent id of a process already attributable, whatever session or group it has moved
to (a plugin's `setsid` child is still the run's). The run's class comes from whichever came first,
the cancel or the deadline; if both hold at the first look, cancel.

| Published bound (`trestle.common.clock`) | Default | Meaning |
|---|---|---|
| `grace` (10 s) | `TRESTLE_CANCEL_GRACE_S` | SIGTERM to SIGKILL |
| `kill` (5 s) | `TRESTLE_CANCEL_KILL_S` | SIGKILL to confirmed gone |
| `stop_bound` (15 s, `grace` + `kill`) | derived | from the stop decision to the tree gone |
| `poll_interval` (0.05 s) | fixed | how often the supervisor looks for a cancel |

A cancel is seen within one `poll_interval`, so a stop completes within `stop_bound` plus
`poll_interval` of the request. By the terminal row every process the run started is gone, or the
answer says it could not be confirmed: `cleanup.processes` on the run frame is `released` only
when the supervisor confirmed the group gone, otherwise `unknown` (never a clean claim without
confirmation, and never `nothing_created` for a run that spawned a process). The evidence and the
result are written only after that confirmation, so nothing changes them after the terminal row on
a stop path.

After a server crash, recovery reads the run's recorded process identities before it marks the run
`interrupted`: it stops the run's processes when the recorded leader is still alive, and it never
signals a pid that now belongs to another process. A run started before identities were recorded
gets no signal at all; its answer reports the stop as unconfirmed.

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
| Run-scoped `query` for an older run | `projection.outside_window` | The run exists but lies past the recency window; it cannot be reached by `query` |
| Unknown view name | `projection.invalid_view` | Read `trestle://views`; do not invent names or send SQL |
| Empty catalog | `admission.plugin_not_found` | `trestle init` or `publish_plugin` |
| Bad plugin args (including a naive or malformed date/datetime) | `admission.invalid_args` | `describe_plugin` then retry `run` |
| Pack plugin `valid: false` | `admission.import_failed` | `pip install -e ".[packs]"`; check `catalog_hint` |
| Reused idempotency key, different args | `admission.idempotency_key_conflict` | New key or same args |

<!-- K-1 -->
### The same idempotency key joins its run after a republish (K-1)

Re-issuing a key with the same plugin and the same arguments returns the run that key named, even when the plugin was republished in between: no second run starts, and the answer is that run's own. The answer says which code ran: `outcome.identity.snapshot_id` is the snapshot the run was admitted against, not the plugin's current one, so a retry after a republish still shows the original id. A different plugin or different arguments under the same key is still `admission.idempotency_key_conflict`.

The key also stays valid for as long as the run can: it expires the `TRESTLE_IDEMPOTENCY_TTL_S` window (default one hour) after the run's admitted deadline plus the finalization margin, not after admission, so a retry that arrives while a long run is still going joins it.

This is a knowing change (K-1): before it, any code change made the same key a conflict, and the key could lapse while its run was still going. It is on by default (`JOIN_ACROSS_REPUBLISH` in `trestle/server/admission.py`).
<!-- /K-1 -->

---

## Operator vs agent

| Role | Surface | Notes |
|------|---------|-------|
| **Agent** | MCP ten tools (`trestle serve`) | Retrieval-first; bounded slices |
| **Operator (CLI)** | `trestle doctor`, `pin`, `unpin`, `recover` | Retention and diagnostics |
| **Operator (HTTP)** | `trestle ops serve` + console UI | Read-only sessions/telemetry — [`operator-sessions-telemetry.md`](operator-sessions-telemetry.md) |

Agents reach evidence **only** through MCP. Operator HTTP does not expose `run`.

---

## Stopping a run: what is not covered

Cancel and deadline stop the processes Trestle can attribute to the run. Two
boundaries are outside that guarantee (full statement in
[`security.md`](security.md)):

- A descendant that **double-forks** out of attribution between two ancestry
  snapshots is outside the containment and evidence-integrity guarantee.
- A run started by a version that **recorded no process identity** (K-19) is
  finalized `interrupted` after a restart, its stop is reported **unconfirmed**,
  never clean, and no signal is sent. Before first starting a version that
  records identity, stop the server with no run live, or check
  `ps -ax -o pid,command | grep trestle.child.main` and stop any such process.

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
