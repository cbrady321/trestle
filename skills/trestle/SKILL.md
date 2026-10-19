---
name: trestle
description: >-
  Get the most out of the Trestle service: run whole local jobs (tests, builds, Docker stacks,
  migrations, pipelines) in one MCP call, chain dependent steps without polling, read bounded
  answers instead of raw logs, and write plugins that run unattended. Use before running pytest
  or any multi-step local job when the trestle tools (run, query, fetch, await_runs) are present;
  triggers: run tests, test step, repo_tests, pipeline, trestle, plugin.
---

# Trestle

Trestle runs a whole local job (a Python plugin) as **one MCP tool call** and answers with a short,
bounded summary; the full output stays on disk behind handles. Every tool call re-sends your whole
context, so one call per *objective*, not per command, is where the savings are.

Reference: [`docs/agents.md`](../../docs/agents.md) (agent guide),
[`docs/plugins.md`](../../docs/plugins.md) (writing plugins), [`docs/packs.md`](../../docs/packs.md)
(Docker / pytest / migration packs).

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

```python
from trestle.plugin import Blocked, Context, trestle


@trestle(deadline=1800, packages=["my_pack"], stop_grace=5, summary_fields=["prs[id,state]"])
def watch_prs(ctx: Context, repo: str) -> dict[str, object]:
    while not ctx.cancelled and ctx.remaining() > 30:
        who = ctx.exec(["gh", "pr", "list", "--repo", repo, "--json", "number,state"], timeout=60)
        if not who.ok and "auth login" in who.stderr:
            raise Blocked("gh_login", "Run `gh auth login`, then resend the same request.")
        ...  # one status artifact, rewritten each round: ctx.attach(path, name="status.md")
    return {"prs": [...]}
```

- **`ctx.exec(argv, ...)`** instead of `subprocess`: a timeout defaulting to the time left, the
  command's whole process group killed on timeout or stop, bounded output, one reused artifact.
- **`raise Blocked(code, human_action)`** instead of failing when a person must act.
- **`stop_grace=`** gives a few seconds after a cancel or the deadline to write a final status;
  loop on `ctx.cancelled`.
- **Re-attach under one name** (`status.md`) to report progress; it holds one artifact slot.
- **Declare `packages=`** for every non-stdlib import (publication warns otherwise), and return a
  parameterized type (`dict[str, str]`, not `dict`).
- The context is thread-safe; `ctx.progress(message, fraction=...)` reaches the caller live.
- **Test without a server:** `trestle.plugin.testing.FakeContext` and `call_plugin`.
- Settings a plugin reads come from the operator's `[plugins.env]` in `config.toml`, never from
  your shell.

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
