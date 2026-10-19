---
name: trestle
description: >-
  Get the most out of the Trestle service: run whole local jobs (tests, builds, Docker stacks,
  migrations, pipelines) in one MCP call, chain dependent steps without polling, read bounded
  answers instead of raw logs, and write plugins that run unattended. Use before running pytest
  or any multi-step local job when the trestle tools (run, query, fetch, await_runs) are present;
  triggers: run tests, test step, repo_tests, pipeline, trestle, plugin. Guidance: the
  trestle://guide MCP resource.
---

# Trestle

## Why Trestle exists

An agent that does local work through a shell pays three costs that grow with the job:

- **Context.** Every tool call re-sends the whole conversation. A test run driven by hand (start
  it, poll it, read the log, grep the failure, re-run) is five or ten calls, each carrying
  everything before it, and the log itself floods the context with output nobody needed.
- **Supervision.** A command started from a shell has no deadline, no clean stop and no record.
  If it hangs, the agent waits; if the agent's session ends, the command is orphaned or lost;
  if two agents start the same database, they collide.
- **Judgment.** Raw output has to be read and interpreted every time, by the most expensive
  reader there is, and the interpretation is lost when the conversation ends.

Trestle moves that work out of the conversation. A **plugin** is a Python function that carries a
whole objective (bring up the stack, migrate, run the tests, judge the result). The per-user
**service** runs it with a deadline it enforces, kills everything the run started when it ends,
serializes runs that share an environment, keeps the full evidence on disk, and answers with a
short verdict. The agent makes one call and reads one answer.

## How to think about using it

Before any local work, ask four questions in order:

1. **What is the objective, not the command?** "The tests touching these files pass" is an
   objective; `pytest tests/a.py` is a step toward it. Find (`list_plugins`) or propose a plugin
   that owns the whole objective, so the steps, retries and judgment happen inside one run.
2. **Can it be one call?** Prefer `completion="terminal"` (the call returns the finished run).
   Steps that depend on each other become one tree plugin, or runs chained with `after=`, sent
   together, not calls you sequence while you wait.
3. **What is the smallest thing I need back?** Usually the verdict (`answer.outcome`) and the
   summary. Fetch a log slice only to fix a specific failure, never to confirm a pass.
4. **Has it already been done?** Name the work with an `idempotency_key`; the same key and
   arguments return the earlier answer instead of running again.

Then let the answer decide the next move: `passed` means move on; `failed` means read the named
failure and fix it; `blocked` means a person must act, so report the action and stop; a refusal
means the request was wrong and nothing ran. Never fall back to doing the plugin's work in your
own shell: if Trestle cannot do it, say so.

**Guide: `trestle://guide`.** An MCP resource on the Trestle server (read it with your host's
resource reader, server `trestle`). It lists every guide resource, one line each: which query
view to use, writing plugins and packs, the public API and complete examples. Read only the one
you need. For people: [`docs/agents.md`](../../docs/agents.md) and
[`docs/packs.md`](../../docs/packs.md).

## Ground rules

1. **MCP only.** Use the ten Trestle tools; never run `trestle`, or the work a plugin does, in your
   own shell. If the tools are missing or the service does not answer, say so and stop: the
   operator starts it with `trestle service start`. Never start a server yourself.
2. **Plugins are not tools.** Run them with `run(plugin="name", args={...})`. `list_plugins`
   names them; `describe_plugin(plugin_id=...)` gives the input schema and `deadline_s`.
3. **Refusals are not runs.** An `admission.*` or `publication.*` answer has no `run_id`; fix the
   request (the message says how; `retryable` says whether resending can help) instead of
   retrying blindly.
4. **Verdict from the answer.** Read `answer.outcome` (`passed`, `failed`, `blocked`, `cancelled`,
   `timed_out`, `execution_error`), not `state`: `state` reads `succeeded` whenever the plugin
   returned.

## Maximize impact: one call per objective

| Instead of | Do |
|---|---|
| run, poll, read a log, re-run | `run(..., wait_ms=1000, completion="terminal")`: one call returns the finished run |
| several commands you sequence by hand | one plugin that carries the objective (steps, branching, retries, final check), or a **tree plugin** whose parts declare `needs` and run in parallel |
| waiting for step A, then calling step B | send B now with `after={"key": "<A's key>", "when": "succeeded", "match": {"ok": true}}`: it is held (no slot, no deadline spent) and starts only if A ends that way |
| re-running a gate you already ran | `idempotency_key="gate:<name>:<commit>"` (and `idempotency_ttl_s` up to a week): the same key with the same args joins or returns the earlier answer |
| reading the whole log to confirm a pass | the `summary` (`ok`, counts, one line per failure); fetch only the slice you need to fix something |
| finding a run again later | `query(view="run_by_key", params={"idempotency_key": "..."})` |

**Chaining example** (plugin names illustrative: lint, then tests, then a Docker check, sent at once; each starts only if the
previous one passed):

```
run(plugin="repo_lint",  args={...}, idempotency_key="pr42:lint:abc123")
run(plugin="repo_tests", args={...}, idempotency_key="pr42:tests:abc123",
    after={"key": "pr42:lint:abc123", "when": "succeeded", "match": {"ok": true}})
run(plugin="docker_stack", args={...}, idempotency_key="pr42:stack:abc123",
    after={"key": "pr42:tests:abc123", "when": "succeeded", "match": {"ok": true}},
    wait_ms=1000, completion="terminal")
```

An unmet condition ends the held run `cancelled` with `execution.after_unmet`. If you find
yourself repeating a chain of calls, propose a plugin (or a tree) for it instead.

**Waiting.** `completion="terminal"` holds the call until the run ends (the run's deadline plus
the finalization margin, `max_call_duration_s` on `describe_plugin`). If your host cuts tool calls
off sooner (some drop a silent call after 300 s), send the run bounded (`wait_ms=1000`) and wait
with `await_runs(run_ids=[...], timeout_ms=285000)` in a loop, or by key with
`await_runs(run_ids=[], keys=[...])`. Never `await_runs` a run you already waited on terminally.

## Running tests: `repo_tests`

```
run(plugin="repo_tests",
    args={"repo": "<absolute repo root>",
          "changed": ["<source files you changed>"],
          "files": ["<extra test files or node ids>"]},
    wait_ms=1000, completion="terminal")
```

- `changed` runs every test file that names a changed module, plus the repo's always-files.
- Options: `since="HEAD~1"`, `frozen_at="<commit>"`, `keyword`, `pytest_args`, `max_failures`.
- `suite=true` runs everything: for a gate, not an edit loop.
- A test failure is a result (`ok: false`); a run state of `failed` or `worker_exit` means setup
  broke: report it, do not retry in a loop. `exit_code` 5: nothing matched.
- In reports give the `summary` line and `files_run`, never the raw output.

## When a run ends `blocked`

A plugin that needs a person (an expired `aws sso login`, a missing tool) ends `blocked`:
`answer.primary` carries `code` (a plugin's own codes read `plugin.<code>`), `human_action` and
`resend`. Report the human action word for word and stop; resend the same request only after the
action is done and `resend` is `succeeds_after_action`.

## Retrieval, smallest first

`fetch("<run_id>/result")` for the return value · `query(view="last_error")` for a failure ·
`query(view="run_tail")` for stdout · `query(view="run_events")` for structured events ·
`query(view="run_artifacts")` then `fetch` an artifact handle with a window (`head`, `tail`,
`grep`, `range`). Handles are opaque (`r_…`, `art_…`), never filesystem paths. The view catalog is
the MCP resource `trestle://views`.

## Writing plugins that run unattended

Learn the surface from the server, one small read at a time, never from Trestle's source or
`~/.trestle`; all of it is under `trestle://guide`. Start at `trestle://guide/plugins`, a short
index, and read only the topic you need (`trestle://guide/plugins/{topic}`).
`trestle://guide/api` lists the public names, and `trestle://guide/api/{module}.{name}` gives
one name's signature. `trestle://guide/examples/{name}` is a complete plugin that publishes
as-is. Extend Trestle by writing a plugin or a pack, never by
changing Trestle.

- **`ctx.exec(argv, ...)`**, never `subprocess`; **`raise Blocked(code, human_action)`** when a
  person must act, never a failure.
- Loop on `ctx.cancelled` and `ctx.remaining()`; declare **`stop_grace=`** to write a last status.
- Report progress by **re-attaching one artifact** (`status.md`), not many.
- **Declare `packages=`** for every non-stdlib import; give every parameter and the return a
  parameterized hint (`dict[str, str]`, not `dict`).
- **Test without a server** (`trestle.plugin.testing`) before `publish_plugin`.
- Settings come from the operator's `[plugins.env]`, never your shell.

## When stuck

| Symptom | Next step |
|---|---|
| `admission.plugin_not_found` | `list_plugins`; check `catalog_hint` |
| `admission.invalid_args` | `describe_plugin`, then resend with matching args |
| `admission.import_failed` | the plugin's package is missing from the service: report it |
| `admission.environment_busy` | another run holds that environment and the wait would not fit: resend later, or with a longer deadline |
| `projection.not_finalized` | the run is still going: wait, then read |
| `execution.provenance_mismatch` | a declared package changed since publication: the operator republishes |
| service down / tools missing | report it and stop |
