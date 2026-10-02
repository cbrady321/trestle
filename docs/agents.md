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
| What did a workflow decide? | `RunView.answer`, then `fetch` | `answer.outcome` / `answer.primary`; the rest at `{run_id}/answer` + `head` |
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
| `{run_id}/answer` (the full terminal answer, `answer.detail`) | `range`, `head`, `tail`, `grep` (not `jsonpath`) |
| `summary.handle` (array continuation) | `jsonpath`, `range` |
| `art_…` (text artifact) | `range`, `head`, `tail`, `grep` |

The full answer is stored as one JSON line, so `{"kind": "head", "count": 1}` returns all of it and
`grep` returns at most its first 512 characters. `trestle://views` does not list `{run_id}/answer`
in this release; the handle works all the same.

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

**Long jobs:** call `run(plugin="…", args={…}, wait_ms=<above zero>, completion="terminal")`. One call returns the finished run, bounded by the run's own deadline plus a published margin, and no follow-up call is needed. Polling is for bounded mode only: with the default `completion="bounded"`, a run that outlives `wait_ms` returns a `running` frame and `await_runs` joins it.

**`completion` (optional, default `"bounded"`, which is everything above unchanged).** Pass `completion="terminal"` to make one `run` call return only a finished run: the response follows the run's finalized terminal row (evidence finalized, then `succeeded`, `failed`, `cancelled`, `timed_out` or `interrupted`), never a `running` frame. The call is bounded by the run's own deadline plus a published finalization margin, not by `wait_ms`: a `wait_ms` above zero is accepted and ignored, and `wait_ms=0` (or below) is refused `admission.invalid_args` because a terminal call has to wait; so is any `completion` value other than `bounded` or `terminal`. If the run is somehow not terminal by that bound, the call answers `projection.terminal_wait_exceeded` (a refusal, not a run state; the run itself is unaffected and `await_runs` still joins it). While a `terminal` call is held, other calls (`cancel`, `query`, `await_runs`) are still answered.

**One class per finished run.** A finished run carries one `answer` (the terminal answer: outcome, the deciding node as `primary`, `cleanup`, and the rest of the nodes in `listed`, bounded by the run's summary budget with the remainder behind `answer.detail`); see `docs/agent-console-mcp.md`. `answer.outcome` is the run's class, one of `passed`, `failed`, `blocked`, `cancelled`, `timed_out`, `execution_error`. A finished `RunView` also carries `outcome: {class, code, identity, recovered}`. Its `class` is classified from how the plugin process ended, so it is one of `passed`, `cancelled`, `timed_out`, `execution_error` and never reads `failed` or `blocked` in this release. `code` is the `execution.*` code for an `execution_error` and `null` for the others; `state` keeps its meaning underneath. For a plain plugin `outcome.class` and `answer.outcome` agree. For a workflow plugin (one that calls `run_tree`) they can disagree: read `answer.outcome` (see [Large tasks: the tree](#large-tasks-the-tree)).

<!-- K-8 -->
### A succeeded run leaves no attributable process or run-created container behind (K-8)

When a run ends, Trestle stops the processes it can attribute to the run (its recorded descendants, see the containment boundaries in `docs/security.md`), and that includes a run that **succeeded**: a plugin that returns normally while a child, or a grandchild in its own session, is still running has that process stopped (SIGTERM first, SIGKILL after the grace period) before the run's evidence is finalized. Nothing the run started writes into `evidence/` or changes `result.json` after the terminal answer. The run's class and summary are unchanged (a succeeded run stays `succeeded`), and `cleanup.processes` reads `released` once the stop is confirmed, `unknown` when it could not be.

The same holds for run-created containers. Under the default policy, every container a workflow run created through the container effect facet (`trestle_packs.container`) is stopped and removed when the run ends, and a passed run is no exception. This covers a container that a child step created, because the root run's release covers everything its children created. The release runs in reverse order of creation, after the last step, as part of the run's ordinary release on every terminal path. It stops the container gracefully and then removes it. Its volumes are never removed. A container counts as released only when a final look finds its name absent, so it is certainly not running. `answer.cleanup` counts it under `released` when that look confirms it gone. When the look cannot confirm it, the container counts as `unknown` and `clean` is `false`, never a clean claim. Only containers named with the run's own selector (`trwr-<root run id>-<step path>`) are ever stopped or removed. A container the run found already running is never touched. That guarantee covers the shipped facet: the host runs each recorded release command as written, and the operator's allowlist bounds which executables it may run, not what their arguments address, so a release written by any other adapter is trusted as written (see `docs/environment.md`). A resource declared durable is outside this rule and is reported as `left_durable`. Asking to keep a container running past the run is refused in this delivery.

This is a knowing change (K-8): before it, a process a plugin forgot outlived a succeeded run, and a passing pipeline left its stack running. The process half is on by default (`REAP_ON_SUCCESS` in `trestle/server/conductor.py`), the same kill every other way a run can end has always received. The container half has no switch of its own: it is the run's ordinary release on success, the same release a failed, cancelled or timed-out run gets.
<!-- /K-8 -->

---

## Snapshot identity

<!-- K-2 -->
A plugin's snapshot id (`snap_` plus 16 hex digits) no longer hashes `plugin.py` alone. It covers the
source, the digests of the packages the plugin declares (`@trestle(packages=[...])`), the input and
return schemas, the declared metadata (deadline, summary fields, packages, env arg, secrets and the
summary budget) and the Trestle runtime version. Two publications with equal ids share source, declared-package digests, schemas and
declarations (not undeclared imports, non-`.py` package content or the environment); an edit to a declared package or a declared value gives a new id. Anyone who
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
the cancel or the deadline; if both hold at the first look, cancel. Only the supervisor signals:
a cancel request writes a flag, and the supervisor records the stop (cause and the lane's committed
length) before it stops anything. The release slice and the finalization reserve (10 s each) are
plan defaults disclosed for the maintainer to set, not requirements.

| Published bound (`trestle.common.clock`) | Default | Meaning |
|---|---|---|
| `grace` (10 s) | `TRESTLE_CANCEL_GRACE_S` | SIGTERM to SIGKILL |
| `kill` (5 s) | `TRESTLE_CANCEL_KILL_S` | SIGKILL to confirmed gone |
| `release_slice` (10 s) | `TRESTLE_RELEASE_SLICE_S` | a workflow run's cooperative release, before the kill (0 for a plain plugin) |
| `stop_bound` (25 s, `release_slice` + `grace` + `kill`) | derived | from the stop decision to the tree gone |
| `finalization_margin` (35 s, `stop_bound` + 10 s reserve) | `TRESTLE_FINALIZATION_MARGIN_S` | how long after the deadline a call may still be answered |
| `poll_interval` (0.05 s) | fixed | how often the supervisor looks for a cancel |

A cancel is seen within one `poll_interval`, so a stop completes within `stop_bound` plus
`poll_interval` of the request. By the terminal row every process attributable to the run (the recorded
leader, its recorded group, and the descendants observed by parent id, each with an identity row)
is gone, or the answer says it could not be confirmed: `cleanup.processes` on the run frame is
`released` only when the supervisor confirmed those processes gone, otherwise `unknown` (never a
clean claim without confirmation, and never `nothing_created` for a run that spawned a process). A
descendant that double-forked out of attribution is not counted: the answer can read `released`
while it lives (see [What stopping a run does not cover](security.md#what-stopping-a-run-does-not-cover)). The evidence and the
result are written only after that confirmation, so nothing changes them after the terminal row on
a stop path.

After a server crash, recovery reads the run's recorded process identities before it marks the run
`interrupted`: it stops the run's processes when the recorded leader is still alive, and it never
signals a pid that now belongs to another process. A run started before identities were recorded
gets no signal at all; its answer reports the stop as unconfirmed.

---

## Run capacity

A service runs at most `max_running_runs` runs at once; a run admitted beyond that waits in a FIFO
of at most `queue_depth` runs, its deadline running from admission, and a run that would exceed
both is refused `admission.queue_full` before it has a run id. Both are settable in the operator's
`config.toml` and by `TRESTLE_MAX_RUNNING_RUNS` and `TRESTLE_QUEUE_DEPTH`.

<!-- capacity -->
Defaults: `max_running_runs` = 31 (measured), `queue_depth` = 256 (not measured); ratio bound = 2.0.
<!-- /capacity -->

`max_running_runs` is the value the measurement chose (`tests/tree/capacity_slice_a.json`): the
Slice A workload (every registered workflow, the tree workflows included) was run at 1, 2, ...
concurrent runs on a darwin Python 3.12 host, and the largest number of concurrent runs at which
every run ended `passed` within its deadline, no process of a run outlived its answer, and the p95
run time stayed within the ratio bound times the p95 at one run. The **ratio bound (2.0)** is the
measurement's own degradation bound, a plan default disclosed for the maintainer to set; it is a
ratio of run times, not the append-cost ratio of the record and not a stop bound. `queue_depth`
was not measured: it keeps its provisional value, now final, because a queued run is bounded by
its deadline (it is finalized `timed_out` if still waiting when it passes), not by the depth. The
number is for the recorded machine; a service on other hardware should re-run
`python -m tests.tree.capacity measure` or set its own value.

---

## Composite workflows (trees)

A workflow plugin may declare a **tree**: a root that runs several children, each child a leaf or a
group with its own children, ordered by `needs` (a child starts only after the children it needs
are done). The root is an `AllDeclaration` (every child must succeed) or a `ChoiceNode` (one alternative is selected and run); `plugins.md` has a worked
example ([Composite workflows](plugins.md#composite-workflows)). You run a tree exactly like any
other plugin: `run(plugin="…", args={…}, wait_ms=<above zero>, completion="terminal")` admits the
whole tree as **one run** with **one run id**, and returns one answer for all of it.

### Large tasks: the tree

When a job has many parts (bring up several services, then migrate, then test; a build matrix;
checks that depend on each other), use a registered tree plugin instead of driving the parts one
`run` at a time. The plugin's author declared the parts, their order (`needs`), how many run at
once (`concurrency`) and each part's time budget. Trestle checks the whole declaration and every
budget at admission, before a run id exists, runs independent parts in parallel inside the one
run, and returns one answer for all of them. A request cannot add or reshape nodes. It can only
select among the declared ones when the plugin offers a selector argument (for example
`services: ["postgres"]`); a value outside the declared set is refused
`admission.unknown_identifier`, with no run id.

1. **Discover.** `describe_plugin(plugin_id="…")`. The selector arguments and the environment
   argument are in `input_schema`; `deadline_s` is the deadline the whole tree runs under. Give
   the environment argument a value even when the schema shows a default: admission does not read
   the default and refuses `admission.lease_set_undecidable` without it.
2. **Run once.** `run(plugin="release", args={"env": "dev"}, wait_ms=1000, completion="terminal")`
   returns when the whole tree has finished, bounded by the plugin's deadline plus the
   finalization margin.
3. **Read `answer`, not `state`.**

   > For any plugin that calls `run_tree` (a single leaf or a tree), the verdict is `answer.outcome`.
   > `state` and `outcome.class` describe the plugin process: they read `succeeded` / `passed`
   > whenever the plugin returned normally, even when `answer.outcome` is `failed` or `blocked`.
   > `outcome.class` never reads `failed` or `blocked` in this release. `await_runs(mode="first_failure")`
   > keys on `state`, so it does not wake on a workflow's `failed` or `blocked` answer; join such
   > runs with `mode="all"` and read each `answer.outcome`.

   `answer.outcome` is one of `passed`, `failed`, `blocked`, `timed_out`, `cancelled`,
   `execution_error`. `answer.primary` is the node that decided it: `path` lists the node names
   from the root (`[]` is the root itself), and `code`, `human_action` and `resend`
   (`succeeds_after_action`, `will_not_succeed` or `unknown`) say what to do next. Node classes rank
   `execution_error`, `unencodable_result`, `failed`, `timed_out`, `exhausted` (reported as
   `blocked`), `blocked`, `repaired` (reported as `passed`), `passed`; the deciding node is the
   highest-ranked one, ties broken by the plan's fixed order, so the order in which nodes finished
   never changes it. `answer.root_stop` is set (`cancel`, `release_point` for the deadline,
   `restart`) when the whole tree was stopped from outside. `answer.cleanup` says whether what the
   run created was released (`clean`).
4. **Read the other nodes.** `answer.listed` holds every other node, each with a `listing`:
   `candidate` (it ended on its own), `rolled_up` (a composite, classed from its children),
   `stopped`, `not_started` (it never ran: something it needs did not pass, or the tree stopped
   first) or `unended`. When `listed_count` is larger than `listed`, the rest is behind
   `answer.detail`, the handle `<run_id>/answer`:
   `fetch("<run_id>/answer", {"kind": "head", "count": 1})` returns the full answer as one JSON
   line (window kinds `range`, `head`, `tail`, `grep`; not `jsonpath`).
5. **Fix, then run again.** Do what `primary.human_action` says; do not re-send unchanged when
   `resend` is `will_not_succeed`. Then `run` again with the same arguments. Each leaf observes
   before it acts, so a leaf whose postcondition already holds does not act again; a resource with
   run lifetime (like the markers in the example below) is released at the end of every run, so
   its leaf acts again.
6. **Stop.** `cancel("<root run_id>")` stops every node and releases everything the run created;
   the answer reads `cancelled` with `root_stop: "cancel"`. A cancel addressed to a child is
   refused (see below).

**Worked example.** The `release` tree in [plugins.md](plugins.md#composite-workflows) runs `build`
and `lint` in parallel, and `package` needs both. Here `lint` returns
`Failed("demo.lint_failed", …)` (abridged):

```text
run → {"run_id": "r_…", "state": "succeeded",
       "outcome": {"class": "passed", "code": null, …},     # the plugin process: not the verdict
       "answer": {"outcome": "failed", "root_stop": null,
         "primary": {"path": ["lint"], "listing": "candidate", "node_class": "failed",
                     "condition": "failed", "code": "demo.lint_failed", "human_action": null, …},
         "cleanup": {"clean": true, "released": 2, "unknown": 0, …},
         "detail": "r_…/answer", "listed_count": 3,
         "listed": [
           {"path": ["build"],   "listing": "candidate",   "node_class": "passed", "disposition": "started", …},
           {"path": [],          "listing": "rolled_up",   "node_class": "failed", "code": "demo.lint_failed", …},
           {"path": ["package"], "listing": "not_started", "node_class": null, …}], …}}
```

`state` and `outcome.class` say the plugin returned normally; `answer.outcome` says the tree
failed. `lint` decided it, `build` passed and what it created was released, and `package` never
started because it needs `lint`. Had `lint` returned `Blocked(code, human_action, resend)`, the
answer would read `blocked`, with that `human_action` and `resend` on `primary`. Had `lint` raised
an exception, the answer would read `execution_error` with `execution.unit_raised`, and every
other node would be listed `not_started` or `stopped`; `state` would still read `succeeded`.

**Failure handling.** A node that fails or blocks stops only the nodes that need it; independent
siblings finish. An exception in any node (`execution.unit_raised`), a `cancel` of the root, or the
root's deadline stops the whole tree. A crash of the worker process ends every node at once
(`execution.worker_exit`).

**Limits and budgets.**
- At most 1024 nodes in the selected scope (`admission.bound_exceeded`).
- The deadline the tree runs under is the plugin's `@trestle(deadline=)` (`deadline_s`, at most
  3600 s). `WorkflowEntry.deadline` in the plugin source is not read in this release.
- Budgets are checked on every run, at admission: a tree whose budgets do not fit its deadline is
  refused `admission.budget_does_not_fit`, naming the node, before a run id. Publication does not
  check budgets, so such a plugin publishes and is then refused on every run; the fix is the
  author's. A started tree can also stop `blocked` with the same code when time spent queued left
  it less than its worst case.
- A carved node still working when its slice ends stops `timed_out` with
  `execution.carve_exceeded`; the nodes below a timed-out composite only release. The slice is
  checked between a unit's calls, so a unit stuck inside one call runs on until the root deadline.
  The root is carved nothing: its slice ends at the release point, which stops the whole run. A
  single-vertex run that reaches it answers `timed_out` with `root_stop` `release_point`, its
  primary `stopped` and carrying the last verdict's code, never `execution.carve_exceeded`.
- Two runs that give the same environment value never run at once: the second waits in the queue,
  or is refused `admission.environment_busy` (retryable, no run id) when the holder's deadline
  leaves it too little time. A plugin that declares no environment is not excluded.
- A composite may declare `gates`: children that only read, checked once before anything in the
  tree acts. A gate that is not satisfied stops its composite with the code it observed, or
  `execution.declaration_stale`.

**Child handles.** No response carries a child handle in this release, so an agent cannot address
a child view. Everything a child view holds (its `path`, `node_class`, `code`, `human_action`,
`resend`) is in `answer.primary` and `answer.listed`, and the full list is behind
`fetch("<run_id>/answer")` (window kinds `range`, `head`, `tail`, `grep`; not `jsonpath`). A handle
per node may be added to the answer later as an additive field.

**Ad-hoc large work: fan out registered plugins.** For work that no registered tree covers, start
independent runs with `run(plugin="…", args={…}, wait_ms=0)` (each returns its `run_id` at once,
`queued` or `running`) and join them with `await_runs(run_ids=[…], mode=…, timeout_ms=…)`:
- `mode="all"` returns when every run is terminal;
- `mode="any"` returns when at least one is;
- `mode="first_failure"` returns when any run's `state` is terminal and not `succeeded`, or when
  all are terminal. It reads `state`, not `answer`, so it misses a workflow's `failed` or
  `blocked`; use `all` for workflow runs and read each `answer.outcome`.

When `timeout_ms` passes first, the call returns the views as they are (some still running): call
it again. An unknown id fails the whole call. Cancel runs one `run_id` at a time. The service runs
at most `max_running_runs` at once and queues up to `queue_depth` more (see
[Run capacity](#run-capacity)); past both, `run` is refused `admission.queue_full`.

Do not author a tree with `publish_plugin` for a one-off task. A tree is a registered workflow:
its author declares what each node may touch, its budgets and its environment, and that
declaration is what admission checks. For ad-hoc work, start independent runs of registered
plugins with `wait_ms=0` and join them with `await_runs`; if the same decomposition recurs, ask
for it to be registered as a tree plugin (`plugins.md`). `publish_plugin` is absent under the
restricted profile (`security.md`). Whatever you publish runs as your user with no sandbox, so a
tree written moments before it runs has had no review of what it touches.

### How a tree run behaves

- **One answer.** The answer has one `outcome` class for the whole tree and one `primary` node
  (its `path` names where in the tree the deciding condition arose). The order in which the
  children finished never changes it, and a shared node starts once. A child that failed only
  stops the nodes that need it; an exception raised in any node, a `cancel` of the root, or the
  root's deadline stops the whole tree.
- **One process, one blast radius.** Every node of a tree runs inside the plugin's one worker
  process (each leaf on its own thread), so an interpreter-level crash in any node (the process
  dies, not a Python exception the loop catches) ends every sibling at once, with no per-node
  stop. The run then ends `worker_exit` and its answer is `execution_error` with
  `execution.worker_exit`; the host releases what the run record names from the record alone, with
  no plugin code. A failure in one backend system's unit is felt by every unit of the tree.
- **Child views.** Every node below the root has a view of its own, which `await_runs` resolves by
  its child handle: the view names its `root_run_id` and its `path`, its `state` is the root's
  state, and its `answer` is that node's account, the same one the root's answer holds. No response
  carries a child handle in this release, so an agent cannot address a child view; read the node
  accounts in `answer.primary`, `answer.listed` and `fetch("<run_id>/answer")` instead. Under the
  restricted profile a child view is read only by the session that admitted its root
  (`projection.not_owner` otherwise).
- **Cancel is addressed to the root.** `cancel` on a child handle is refused with
  `projection.cancel_not_root`; nothing is written and the root runs on. Cancel the root's run id
  instead. This is **open question OQ-27** (what a cancel addressed to a child view should do while
  its root is live: refuse, or act on that child); Trestle ships the assumed answer, the refusal,
  and a maintainer answer of "act on the child" would change it.
- **Root-entry eligibility is open (OQ-31).** Whether a unit whose preconditions only a sibling
  could satisfy may be published as a root is undecided. What is shipped is `admit_and_stop`: such
  a root is admitted and stopped by the loop's own in-node refusal before any effect. That is the
  pre-existing in-node stop, not a decision on the question. Such a run is admitted, takes a run
  id, and ends `failed` with `execution.plan_precondition_uncovered` before any effect.
- **`ChoiceNode` roots select one alternative.** A root (or any node) may be a `ChoiceNode`: the
  loop observes every eligible alternative before the first effect, picks the first one that is
  present (else the declared `fallback`), and records the choice and the plan identity before any
  effect record. The same inputs pick the same alternative; an alternative the choice left out is
  `not_started` in the answer and in its child view. A condition seen at selection (a drifted
  gate, a dependent whose route the chosen alternative cannot reach) or in a node (an absent
  instance, a missing toolchain) stops that node with one class and a code that names the
  identifier. `admission.plan_multi_vertex_unsupported` is retired: no path produces it any more.

The refusal and stop codes a tree run can give (none has a `run_id` unless it is a node's code in a
started run's answer: every `execution.*` code, and `admission.budget_does_not_fit` or
`admission.route_unsupported` when a started tree stops a node with it):

| Code | Meaning | Fix |
|------|---------|-----|
| `publication.unit_unresolved` | A child names a unit the plugin does not declare | Declare it, or fix the name |
| `publication.dependency_cycle` | The `needs` graph, or the containment, loops | Break the cycle |
| `publication.declaration_conflict` | One name is bound to two different nodes, or two references to one node carry different parameters | Use one binding per node |
| `publication.plan_precondition_uncovered` | A leaf's precondition is covered by no node before it | Add the covering node to its `needs`, or drop the precondition |
| `admission.unit_unresolved` | The admitted declaration names a unit that does not resolve | Republish the plugin |
| `admission.dependency_cycle` | The admitted declaration has a cycle | Republish the plugin |
| `admission.declaration_conflict` | The admitted declaration binds one node twice | Republish the plugin |
| `admission.lease_set_undecidable` | The request gives no value for the root's environment field, or a node declares an environment that does not match the root's | Pass the environment argument; declare one environment |
| `admission.unknown_identifier` | A request value names no identifier in the declared set (the refusal lists the valid ones) | Use a listed value |
| `admission.bound_exceeded` | The selected part of the tree has more than 1024 nodes | Select fewer nodes with the selector arguments |
| `admission.budget_does_not_fit` | The tree's budgets do not fit: a child's budget is over its parent's less the 10 s reserve, a composite's budget is under its longest `needs` chain or under ceil(children / `concurrency`) × its largest child, or the root's budget plus the release slice is over the deadline (the message names the node). On a started tree, a `blocked` stop when time spent queued left it less than its worst case | Ask the plugin's author to fix the budgets or the deadline; for the started-tree stop, run again when the environment is free |
| `admission.route_unsupported` | A `ChoiceNode` has no eligible alternative, or a node that needs a choice cannot reach the alternative it would get | Change the selection argument; otherwise ask the author |
| `admission.environment_busy` | Another run holds the environment and its deadline leaves this request too little time (retryable, no run id) | Run again after that run ends |
| `execution.carve_exceeded` | A carved node's time slice ended before it reached its condition; class `timed_out`, and the nodes below a timed-out composite only release. Never the root's: the root's slice ends at the release point, a whole-run stop (`root_stop` `release_point`) | Ask the author for a larger budget, or find why the node is slow |
| `execution.unit_raised` | A unit raised an exception, or broke its contract (for example `Acted` with no effect issued); class `execution_error`, and the whole tree stops | A plugin defect: report it to the author |
| `execution.postcondition_timeout` | A node acted but its postcondition did not hold within its wait; class `exhausted`, reported as `blocked` | Find from the node's evidence why it did not become ready |
| `execution.remedy_exhausted` | A node's declared repair was spent without success; class `blocked` | Find from the node's evidence why the repair did not work |
| `execution.plan_precondition_uncovered` | A root entry needs a precondition only a sibling could cover (the shipped OQ-31 stop); class `failed`, nothing ran | Ask the author to publish the unit inside a composite that covers the precondition |
| `execution.declaration_stale` | The declaration the run was admitted with no longer matches what the plugin declares (or the admitted plan does not verify); nothing ran | Run again; republish if it repeats |
| `projection.cancel_not_root` | `cancel` was addressed to a child | Cancel the root's run id |

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
