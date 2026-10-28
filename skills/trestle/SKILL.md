---
name: trestle
description: >-
  Do local work through Trestle in one MCP call per objective. Use whenever the trestle MCP tools
  (run, query, fetch, await_runs) are present and you are about to run tests, build, run a
  pipeline, or do any multi-step local job: for several steps, write one plugin that chains them
  (skill trestle-pack), publish it, and run it once. Triggers: run tests, build, pipeline, exec.
---

# Trestle

## Why

Every tool call re-sends the whole conversation, so each extra call costs more than the last.
A Trestle run carries the whole objective (every step, retry and check) under a deadline the
service enforces. The answer is one verdict, not a log to read.

## The one move

1. Name the objective, not the command: "the tests for these files pass", not `pytest a.py`.
2. Is there a plugin for it? `list_plugins`, then `describe_plugin(plugin_id=...)` for its
   arguments. If so, run it once: `run(plugin="<name>", args={...}, wait_ms=1000, completion="terminal")`.
3. None, and the job has more than one step? Write one plugin that chains the steps (skill
   `trestle-pack`, example `trestle://guide/examples/chain`), publish it with `publish_plugin`,
   and run it once: two calls the first time, one after that. A chain of calls you repeat should
   be a plugin.

**Long jobs:** one call with `completion="terminal"`; `await_runs` is only for bounded mode (see Waiting).

**Where publishing is off** (no `publish_plugin` tool; the restricted profile): run the
allowlisted plugins and order them with `after={"key": "<earlier key>", "when": "succeeded"}`.
The held run starts only if the earlier one ended with a passing verdict (`answer.outcome` is
`passed`); otherwise it ends `cancelled` with `execution.after_unmet`.

## Finding things

The trestle tools being present means Trestle is present. If they are missing, or every call
fails the same way (connection refused, HTTP 401), say what you saw and stop. Plugins and packs:
`list_plugins`, then `describe_plugin`; names vary by service, so `repo_tests` and `exec` here are
examples only. Limits and load (deadline ceiling, key ttl, free slots): `query(view="status")`.
Everything else: the MCP resource `trestle://guide` (server `trestle`), one topic at a time.

## Reading the answer

Read `answer.outcome`, never `state` (`state` says only that the plugin process finished).

- `passed`: move on. `failed`: read the named step (`answer.primary.code`, the summary) and fix it.
- `blocked`: report `answer.primary.human_action` word for word and stop. Resend only after the
  action is done and `resend` is `succeeds_after_action`.
- `cancelled`, `timed_out`, `execution_error`: the check did not finish; report it.
- A refusal (`admission.*`, `publication.*`) has no `run_id`: nothing ran, and its message says
  what to fix. `retryable: true`: wait `retry_after_s`, then resend. `admission.environment_busy`
  names the run holding the environment (`held_by`): send yours with
  `after={"run": "<held_by>", "when": "ended"}`. All codes: `trestle://guide/plugins/refusals`.

Smallest read first: the summary, then `query(view="last_error")`, then a log slice (`fetch` with
a `tail` or `grep` window). Never read a log to confirm a pass.

## Not doing it twice

Give anything you might send again an `idempotency_key` (`"gate:<repo>:<commit>"`): the same key
and arguments join the live run or return its answer. `query(view="run_by_key")` finds it again.

## Waiting

`completion="terminal"` holds the call until the run ends (at most `max_call_duration_s`). If your
host cuts calls off sooner (some drop a silent call after 300 s), send with `wait_ms=1000`
(bounded mode) and loop on `await_runs(run_ids=[...], timeout_ms=285000)`, or by key with
`await_runs(run_ids=[], keys=[...])`. Never `await_runs` a run you already waited on terminally.

Runs belong to the service: closing the chat never stops one. Cancel a run you did not send only
when it is the `held_by` of your refusal and `query(view="run")` shows `session_ended: true`.

## Fallbacks, last

- `after=` orders your run against one you did not send or one already running:
  `after={"run": "<run_id>", "when": "ended"}`. `when: "succeeded"` waits for a passing verdict.
- `exec` runs one command no plugin owns:
  `run(plugin="exec", args={"argv": ["git", "status"], "cwd": "<absolute dir>"}, wait_ms=1000, completion="terminal")`.
  No shell is added (no pipes, globs or `&&`). Read `summary.ok`, `exit_code`, `stderr_tail`. If
  `exec` is missing or not allowlisted, say so. A command you send again and again is a plugin.

## Ground rules

MCP only: never run `trestle`, or the work a plugin does, in your own shell. Plugins are not
tools: run them with `run(plugin="<name>", ...)`. Starting, stopping and configuring the service
is the operator's job. A message that says what "the operator runs" is for the operator: report
it word for word and stop. Never run it yourself, through `exec` or any other way. Extend Trestle
by writing plugins and packs (skill `trestle-pack`), never by changing it or reading its source.
