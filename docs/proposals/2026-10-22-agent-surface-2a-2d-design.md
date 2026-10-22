# Agent surface, changes 2a to 2d: design

**Status:** design, ready to implement. Nothing here is built. The four owner questions are
decided ("Owner decisions (2026-10-22)", at the end).
**Date:** 2026-10-22
**Release:** one bundle, 0.6.0, on top of 0.5.0 (credentials change 3).
**Record:** [`docs/decisions/service-and-credentials.md`](../decisions/service-and-credentials.md),
implementation sequence rows 2a, 2b, 2c and 2d.
**Read at:** master `fc4999d0` (the code these changes land in), and change 3 on branch
`credentials-store` at `b2c64bd0` (worktree `.claude/worktrees/agent-a8da8e6b3a0c5c6ba`). Every
file, function and line cited below was read at those commits. Line numbers are for `fc4999d0`
unless marked "change 3".

---

## Summary

The bundle adds no MCP tool, no new error code and no new run state. It adds one query view, four
optional fields on existing answers, one operator command, one seeded plugin and a docs pass.

1. **A session has ended** when the client deletes it (an HTTP DELETE answered 2xx), or when the
   SDK has expired it (no request in flight for 30 minutes, or a request on it answered 404). A
   dropped connection is not an end. fastmcp 3.4.7 gives no hook for either, so Trestle watches
   its own ASGI stack and mirrors the SDK's idle rule using only the SDK's public constants.
2. **The record of it** is one small file per server, `home/servers/<server_id>.sessions.json`,
   written only by that server. Not a ledger row. A run's `created.owner` names the file to read.
   After a restart nothing needs recording: every run the old process owned has been reaped.
3. **It shows** as `session_ended` (true, false, or null once the run has ended) on the
   `recent_runs` and `run` view rows, as `runs.session_ended` in the new `status` view, and as a
   `session_ended_runs` section in `trestle doctor`. It changes nothing about the run.
4. **Cancel:** under the restricted profile an MCP session may cancel a run it received, or any
   run whose session has ended. Child-view reads follow the same rule. The full profile is
   unchanged. `trestle cancel <run_id>` is the operator's unscoped cancel (exit 0, 1 or 2).
5. **Refusals** gain `retry_after_s` (whole seconds) on every retryable refusal, filled from one
   table so no refusal site can forget it, and `admission.environment_busy` gains `held_by` (the
   run whose deadline frees the environment last) and `free_at` (that deadline, ISO 8601 UTC).
6. **`query(view="status")`** returns one row of at most 4 KiB: version, profile, draining, limits
   (deadline ceiling, max key ttl, pool and queue sizes), slots, live runs by state, the
   environments in use with `held_by`/`free_at`, plugin counts, missing plugin folders. Never
   credentials. `list_plugins` marks `allowlisted` on each item under the restricted profile.
7. **The `exec` plugin** is a guide example (`trestle://guide/examples/exec`) that `trestle init`
   seeds into the home's plugins folder. It wraps `ctx.exec`: `argv` and an absolute `cwd`, which
   must resolve under a root listed in `[plugins.env] TRESTLE_EXEC_ROOTS`. It answers `ok`,
   `exit_code`, `timed_out`, two 1,500-byte tails and the output artifact. Under the restricted
   profile it runs only when the operator lists it. A credential declaration on a plugin named
   `exec` is refused at publication.
8. **Docs and skills** name the status view, the refusal fields, `exec` and the session rule, and
   tell an agent never to run `trestle`, or any command addressed to the operator, whether in its
   shell or through `exec`. Such a command is quoted only as `the operator runs: <command>`, for the
   agent to relay. A doc test and a message test enforce both (owner decision 3).
9. **`on_session_lost` is out of 0.6.0.** 2a leaves one seam for it (`McpSessions.end`). It needs
   its own decision on the idle timeout first.

---

## 2a: orphan visibility and cancel

### What the library actually gives

The server builds its app with `mcp.http_app(transport="streamable-http", middleware=[...])`
(`trestle/server/main.py:271-275`). In fastmcp 3.4.7 that app's lifespan creates a
`FastMCPStreamableHTTPSessionManager` (`fastmcp/server/http.py:43-75`, created at `:658`), a
subclass of the mcp 1.30.0 `StreamableHTTPSessionManager`. Neither `http_app` nor the subclass
takes `session_idle_timeout` (no occurrence in the fastmcp package), so the SDK default applies:
`DEFAULT_SESSION_IDLE_TIMEOUT = 30 * 60` (`mcp/server/streamable_http_manager.py:42`, in that
module's `__all__`).

How a session ends in the SDK:

- **Client DELETE.** `_handle_stateful_request` (`:257`) passes the request to the transport,
  whose `_handle_delete_request` calls `terminate()` and answers 200; the manager then calls
  `_discard_session` (`:291`).
- **Idle expiry.** The transport counts requests in flight (`mcp/server/streamable_http.py:418-443`):
  a POST being answered and an open GET stream both count. When the count reaches zero its
  `idle_scope` deadline is set to now plus the timeout. When it fires, `end_when_idle` (`:354`)
  calls `_discard_session`. From then on the session id answers 404 (`:310`).
- **Crash** of the session's server task: `_discard_session` in `run_server`'s `finally`.
- **Server shutdown:** fastmcp's lifespan `finally` terminates every transport.

Every path ends in `_discard_session` (`:392`), a private method on an object fastmcp constructs
inside a closure. There is no public hook, no callback and no event. fastmcp's middleware hooks
(`on_message`, `on_request`, `on_initialize`, ...) have no session-end hook either.

A **dropped TCP connection is not a session end.** Streamable HTTP sessions outlive connections
by design. A closed POST stream ends that one request; the tool call goes on in the session's
task group and its answer is dropped. When the host process dies, its connections close, the
in-flight count drops to zero, and the session expires 30 minutes later.

The session id Trestle records is the same string: `Context.session_id`
(`fastmcp/server/context.py:637-691`) reads the request's `mcp-session-id` header, which is the
transport's `mcp_session_id`, and `_caller_session()` (`trestle/server/main.py:171-178`) passes it
to `run` and `cancel`.

**Fallback taken: Trestle observes its own ASGI stack.** Trestle already wraps the MCP app in its
own middleware (`BearerTokenMiddleware`, `_InFlight` at `main.py:237-256`). Every request for a
session passes through that stack, so a Trestle middleware sees exactly what the SDK sees:
the session header, the method, and the status of the answer. Trestle can then mirror the SDK's
idle rule using public constants only (`MCP_SESSION_ID_HEADER` from
`mcp.server.streamable_http`, `DEFAULT_SESSION_IDLE_TIMEOUT`). Monkeypatching `_discard_session`
would catch every path, crash included, but it is private, and a rename would silently disable
the feature. A pin test (below) fails if the SDK's default or fastmcp's use of it changes.

### What counts as "session ended"

A session S that this server opened ended at the first of these:

| Reason | Event | Seen how |
|---|---|---|
| `deleted` | a DELETE carrying S is answered 2xx | the middleware, before it forwards the answer |
| `expired` | S had no request in flight for `SDK_IDLE_TIMEOUT_S + EXPIRY_GUARD_S` (1,800 + 15 s), or a request carrying S was answered 404 | the middleware's counts, checked on the reaper's 10 s pass; the 404 at once |
| `server_gone` | the server that opened S is not running | computed by readers from the owner's server lock; never written |

The 15 s guard makes Trestle mark S only after the SDK has discarded it: the SDK's countdown
starts when its own count reaches zero, a moment before the middleware's does. So once Trestle has
marked S, any later request on S gets a 404 and cannot contradict the mark. The mark never
reverses.

These are **not** an end: a dropped connection, a `notifications/cancelled`, a closed GET stream,
an abandoned `run(completion="terminal")` call, or a host that exits without DELETE (until the
idle expiry). Whether Claude Code, Cursor and Claude Desktop send DELETE when they exit is not
verified. Assume the common case of a host dying is seen about 30 minutes later.

### Where it is recorded

**Not a ledger row.** The ledger is one run's record with one writer, its owner. A session spans
many runs and ends outside all of them. Writing rows into runs' ledgers from the HTTP layer would
add a second writer and a scan. The mark is information about the server's client, so it lives
with the server that knows it.

**`home/servers/<server_id>.sessions.json`**, written whole with `atomic_write_json` by the server
that opened the sessions, and by nothing else:

```json
{"v": 1, "server_id": "s_…", "ended": {"<session id>": {"at": "2026-10-22T10:00:00Z", "reason": "deleted"}}}
```

- An entry is added when the session ends, and kept while any live run this server owns names it
  as `caller_session`. An entry is dropped once no owned live run names it and it ended more than
  `ENDED_KEEP_S` (60 s) ago. The 60 s covers a `run` admitted on the admission thread while its
  session's DELETE was being answered. The file is removed when `ended` is empty.
- Only ended session ids are written. They are dead (the SDK answers 404 for them), so the file
  hands nothing usable to a reader. A live session id is never written anywhere new.
- `ServerLock.close()` (`trestle/server/home.py:478-481`) and `live_servers(prune=True)`
  (`home.py:484-503`, where a dead server's lock file is unlinked) also unlink the
  `.sessions.json` beside it.

The run's `caller_session` is read from `evidence/state.json`. `state_record`
(`trestle/server/ledger.py:113-154`) gains `"caller_session": created.get("caller_session")`, so
listings keep reading the small file (v0.3.1 Problem C). It is on disk only, never on the wire.
No agent-facing answer carries a session id.

### Restarts and multiple servers

A restart ends every session the old process served: the SDK keeps sessions in memory, so a
client's next request with an old id gets 404 and must initialize again. **Nothing is written at
start.** Every run the old process owned has had its owner lock freed. The reaper finalizes those
runs `interrupted`, so no live run can name a session from before the restart. Between the
process's death and the reaper pass, readers answer `server_gone` from the free owner lock. The
dead server's `.sessions.json` is deleted with its lock file. Several servers on one home (v0.3.1)
each write their own file. A run's `created.owner` (`admission.py:696`) says which file to read.

### The modules

New module `trestle/server/mcp_sessions.py`. It is not called `sessions.py`: `trestle/ops/sessions.py` is
a fence `leave` target (`tests/proof/fence.toml`), and the ops console's "sessions" are runs.

- Constants: `SDK_IDLE_TIMEOUT_S = DEFAULT_SESSION_IDLE_TIMEOUT` (imported),
  `EXPIRY_GUARD_S = 15.0`, `ENDED_KEEP_S = 60.0`, reasons `DELETED = "deleted"`,
  `EXPIRED = "expired"`, `SERVER_GONE = "server_gone"`.
- `sessions_path(home, server_id) -> Path`.
- `SessionEnd(at: str | None, reason: str)`, frozen.
- `session_end(home, owner, session) -> SessionEnd | None`. None when `session` or `owner` is not
  a string. `SessionEnd(None, SERVER_GONE)` when `servers/<owner>.lock` is not locked
  (`home.is_locked`). Otherwise the entry from the owner's file, or None. An unreadable file reads
  as "not ended".
- `session_ended(home, record) -> bool | None` over a state record (`state`, `owner`,
  `caller_session`): None for a terminal run or one with no `caller_session`, else whether
  `session_end` found one. `SessionReader(home)` memoizes each owner's file and lock probe for one
  query or one doctor report.
- `class McpSessions(home, server_id, ownership)`. This is the serving process's watch and the
  file's only writer. It is thread safe, with one `threading.Lock`, because the event loop and the
  reaper thread both call it.
  - `opened(sid)`, `request_started(sid)`, `request_finished(sid)`: the in-memory
    `{sid: (inflight, idle_since)}` table. `request_started` on an unknown sid that has not ended
    adds it, as a defensive measure.
  - `end(sid, reason)`: idempotent. It acts only for a sid this server opened. It moves the sid to
    `ended` and rewrites the file. **This is the seam `on_session_lost` will use.**
  - `sweep()`: ends sessions idle past the bound, then prunes `ended` against the owned live runs
    (`ownership.owned()`). Each run's `caller_session` is read once from its state.json and cached
    by run id; the cache drops runs no longer owned.
- `class SessionWatch` (ASGI middleware), the innermost entry of the list in `_http_server`
  (`main.py:273`): `[Bearer, _InFlight, SessionWatch(sessions=kernel.sessions)]`. It sits behind
  the token check, so an unauthenticated request never touches it. For an `http` scope:
  - With an `mcp-session-id` request header: `request_started` before the app runs, and
    `request_finished` in `finally`. In the `send` wrapper, on `http.response.start`: a DELETE
    with status 2xx calls `await asyncio.to_thread(sessions.end, sid, DELETED)` before the message
    is forwarded, and a 404 calls `end(sid, EXPIRED)` the same way. A client that has seen the
    DELETE's 200 therefore always finds the mark. This makes the tests deterministic.
  - Without one: on `http.response.start` with a status below 400 and an `mcp-session-id`
    response header (the `initialize` answer), `opened(new_sid)` before it is forwarded.

Wiring in `Kernel.start_service` (`main.py:61-76`): create `self.sessions = McpSessions(home,
server_id, ownership)`, and chain `reaper.on_pass` (set to `scheduler.seen` at `main.py:133`) so
each 10 s pass also calls `sessions.sweep()`. `Kernel` gains `sessions: McpSessions | None = None`.
`_http_server` passes `kernel.sessions` to the middleware. A CLI kernel never has one.

### How it shows

- **`recent_runs` and `run` rows** gain `session_ended: bool | null`
  (`VIEW_ROW_FIELDS`, `trestle/query/views.py:28-62`; `_recent_run_row` and `_run_row`,
  `trestle/query/fs.py:522-541`). `RunRecord` (`fs.py:38-53`) gains `owner` and `caller_session`,
  read in `_record_from_state` and `_load_run_record`. The value is true or false for a live run
  with a session, and null for a run that has ended or was admitted outside MCP. It is null after
  the end because the mark only matters while the run is live, and because that is what lets the
  file be pruned. The `trestle://views` text for both views says so (`trestle/query/catalog.py`,
  `_VIEW_WHEN`).
- **`trestle doctor`** gains a `session_ended_runs` field on `DoctorReport`
  (`trestle/server/doctor.py:25-102`), built from the live markers (`live_run_ids`, then each
  run's state.json), and these lines after `live_runs`:

  ```
  session_ended_runs: 2
    - r_20261022T100000Z_ab12 repo_tests running: session deleted at 2026-10-22T10:00:00Z
    - r_20261022T100500Z_cd34 echo held: server gone
  ```

  At most 20 are listed, then `  … and N more`. Health is unchanged: such a run is still a live
  run, and live runs already make health `degraded`.
- **`status`** counts them as `runs.session_ended` (2b).

### The cancel rule

One predicate, `Project._in_scope(created, caller_session) -> bool`, replaces the two inline
checks, in `Project.cancel` (`trestle/server/project.py:366-374`) and in `_resolve_child`
(`project.py:531-539`):

- Full profile (`session_scoped_cancel` false), or `caller_session` None (the operator's CLI or
  console): always in scope. This is unchanged.
- Restricted profile: in scope when `created.caller_session == caller_session`, **or** when
  `created.caller_session` is a string and `session_end(home, created.owner,
  created.caller_session)` is not None. A run with no `caller_session` (admitted outside MCP)
  stays out of scope for every MCP session. Only the operator can cancel it.
- Out of scope is still `projection.not_owner`, checked before the state as today, so a foreign
  run's state is not revealed. The message becomes `run not received in this session, and its
  session has not ended: <run_id>`.
- Child-view reads use the same predicate. A session that may cancel a root may read its
  children. One predicate is less code than two, and a cancel without the read would be odd.

Under the restricted profile this also fixes a gap. Before, an agent whose session expired and
reconnected could not cancel its own earlier runs: the new session id differs. Now it can, and so
can anyone else.

### `trestle cancel <run_id>`

`trestle/cli.py`: a subparser `cancel` with a positional `run_id` and `--home` (`_add_home_arg`).
`_cancel_cli` follows `_pin_cli` (`cli.py:241-246`): `create_kernel(home, skip_recovery=True)`,
then `kernel.control.cancel(run_id, None)`. That is the unscoped path. It writes the cancel flag
(`RunRegistry.request_cancel`, `trestle/server/runs.py:53-59`), and the owning server's
supervisor or scheduler pass acts on it, as for a cancel received by any other server.

- Accepted: print the outcome's message (`cancel accepted for <run_id>`) on stdout, exit 0. If the
  run's owner lock is free, also print `its server is not running: the run ends interrupted at the
  next reaper pass (trestle recover runs one now)`.
- Refused: print `trestle cancel: <message>` on stderr and exit 1. That covers an unknown run, a
  run that cannot be cancelled because it has ended, and a child handle (the message names the
  root).
- A refused home or a usage error: exit 2 (the existing `HomeRefused` handler; argparse).
- There is no `--wait` and no list form. A stop completes within `stop_bound` (25 s) of the flag
  while its owner is alive. `trestle doctor` shows what is left.

### The dropped terminal wait

The record calls this unverified. Most of it is already proven: `tests/single/control/mcp/test_sever_rejoin.py`
holds a `run(completion="terminal")` and severs it by `cancel_notification`, `session_close`
(DELETE) and `sigkill`, and shows that the run ends by its own deadline and that a re-send joins
it. Two cases are missing:

- **A raw connection drop**: the client closes the socket, with no DELETE and no notification.
  This is what a killed host does. The proof host gains this sever mode.
- **A run that ends on its own**, so the test shows the outcome is untouched (`succeeded`,
  `passed`, no stop) and not merely "ended by its deadline".

Both are in the test list below. Reading the code, the expected result holds: the tool call runs
in the session's task group, not in the HTTP request's task. `await_terminal_async`
(`project.py:190-209`) only polls. When the POST stream closes, the answer is dropped and the
polling coroutine runs until the run is terminal, which is bounded by deadline plus margin. It
holds nothing the run needs.

### `on_session_lost`

**Out of 0.6.0**, as the record says. It is its own small change after 2a. Two reasons to keep it out:

- It adds a decorator keyword. That means a manifest field, the snapshot identity, `describe_plugin`
  and goldens: a separate review.
- Its motivating case is a run holding an environment lease for an interactive session. That case
  is served badly by a 30-minute idle expiry: when a host is killed, `"cancel"` would fire half an
  hour late. The change has to settle the idle timeout first. Owner decision 2 keeps 30 minutes
  in 0.6.0 and leaves the question to this change.

2a leaves the seam: the follow-up calls the conductor from `McpSessions.end(sid, reason)` for the
owned live runs that name `sid` and whose snapshot declares `"cancel"`.

---

## 2b: the status view and the refusal fields

### Refusal fields

`RequestOutcome` (`trestle/common/types.py:13-25`) gains three optional fields:

| Field | Type | On the wire |
|---|---|---|
| `retry_after_s` | `int \| None` | **always when `retryable` is true**: the field's value, else `codes.RETRY_AFTER_S.get(code, codes.DEFAULT_RETRY_AFTER_S)` |
| `held_by` | `str \| None` | when set (only `admission.environment_busy`) |
| `free_at` | `str \| None` | when set (only `admission.environment_busy`) |

`to_dict` fills `retry_after_s` from the table, so no refusal site can forget it. The table is
plain data in `trestle/common/codes.py`. The `refusal_codes` facet reads only `str` constants
there, so a dict and an int do not change it. `types.py` imports `codes`; `codes` imports only
`trestle.common.plan.vocabulary`, which imports nothing from trestle, so there is no cycle.

**Meaning.** `retry_after_s` is how long, in whole seconds, to wait before sending the same
request again. A resend sooner is likely to be refused the same way. A resend at or after it can
still be refused, and then carries its own `retry_after_s`.

**Which codes are retryable.** This is unchanged: exactly the sites that set `retryable=True`
today. Every one gets a value:

| Code | Site | `retry_after_s` | Why |
|---|---|---|---|
| `admission.environment_busy` | `Admission._environment_busy`, `admission.py:391-422` | computed (below), at least 1 | the moment the same request would be queued instead of refused |
| `admission.queue_full` | `Scheduler.check_admit_capacity` and `check_hold_capacity`, `scheduler.py:121-146` | 5 | slot turnover is not known; a short, cheap re-check (a refusal mints nothing) |
| `admission.home_busy` | `_home_busy`, `admission.py:124-136` | 2 | one more `ADMISSION_WAIT_S` |
| `admission.service_draining` | `check_admit_capacity`, `scheduler.py:106-115` | 10 | a drain lasts until its runs end. Waiting for the latest deadline could mean an hour, so re-check and read `status` |
| `projection.not_finalized` | `fs.py:143-149` | 1 | finalization follows the terminal row closely; `await_runs` is the better call |
| `projection.cursor_expired` | `fs.py:104-116`, `:222-229` | 0 | resend at once, without the cursor |
| `publication.registry_conflict` | `registry.py:459-464` | 0 | `describe_plugin`, then republish at once |

The record says "every retryable refusal" next to admission codes only. This design reads it as
every `retryable: true` outcome, whatever its origin, because one invariant is easier to state,
document and test.

**The credential refusals** of change 3 (`admission.credential_unknown`,
`admission.credential_not_granted`, `admission.credential_unavailable`) are `retryable: false`
(`credentials.py`, `_outcome`, change 3). They carry none of the three fields. Each needs the
operator, and its message already names what the operator runs. An agent reports the message and
stops. They are checked before the plan and the lock (change 3, "Where admission checks"), so a
request that is both ungranted and environment-busy gets the credential refusal, with no retry
hint. That is right: retrying cannot help.

### `held_by` and `free_at`

The environment "free time" is not computed in `lease.py`: `free_at` is the name of the first
parameter of `lease.leaves_too_little` (`trestle/server/lease.py:52`), and its value comes from
`pools.busy_until(state, key)` (`trestle/server/pool.py:271-282`). That is the latest recorded
deadline among the key's running and queued runs, held runs excluded.

- Add `pools.busy_holder(state, key, *, now=None) -> tuple[str, float] | None` beside it: the
  `(run_id, deadline)` of the entry with that latest deadline (ties: the latest arrival). Express
  `busy_until` through it, so the two cannot disagree.
- **`held_by`** is that run id: the run whose recorded deadline frees the environment last. It is
  not necessarily the run holding it right now. That is what makes
  `after={"run": held_by, "when": "ended"}` correct. The held run takes no slot and spends no
  deadline (admission skips the busy check for a held run, `admission.py:314-319`). It is released
  when the last run in line has ended.
- **`free_at`** is `busy_until` as ISO 8601 UTC, in the same format as `as_of`
  (`%Y-%m-%dT%H:%M:%SZ`). It is the latest the holders may run by their recorded deadlines. Their
  stop and release can take up to `stop_bound` more.
- **`retry_after_s`** is `max(1, ceil(free_at + worst_case + release_slice - deadline_s - now))`.
  That is the inverse of the check in `_environment_busy`: the first moment the same request's
  would-be deadline leaves room after `free_at`, given the recorded deadlines. It can succeed
  sooner if the holders end early.
- The message, kept within 200 characters: `the environment is held by <held_by> until at most
  <free_at>, too late for this request's deadline; wait for it (await_runs, or after=) or resend
  in <n>s`.

**Across sessions under the restricted profile**, `held_by` is shown whoever sent the holder. It
reveals nothing new: root run views, `recent_runs`, `query(view="run")` and `fetch
<run_id>/result` are not session scoped today (`Project.status`, `project.py:110-114`). The
restricted profile scopes cancel and child views only. With 2a, `held_by` plus `session_ended`
is exactly how a blocked agent finds an orphan it may cancel.

### `allowlisted` on `list_plugins`

`PluginCatalogRow` (`types.py:348-362`) gains `allowlisted: bool | None = None`. `to_dict` emits it
only when it is not None. `Project` gains `allowlist: tuple[str, ...] | None = None`, where None
means the full profile. `create_kernel` (`main.py:139-144`) passes `config.profile.allowlist` when
restricted. `Project.list_plugins` (`project.py:472-473`) sets the field on each item under the
restricted profile. `Registry.catalog` stays profile-free. Rows in `failed` get no field.
`describe_plugin` is unchanged.

### `query(view="status")`

**Registration.** `ViewName` (`views.py:9-20`) gains `"status"`, appended last so the enum's
existing positions do not move. `QueryViewArg` (`trestle/server/mcp_schema.py`) picks it up, which
changes `tools/list` (golden S0, divergence entry below). `VIEW_ROW_FIELDS["status"]` lists the
row's 13 keys. It is not in `RUN_SCOPED_VIEWS`, `FINALIZED_VIEWS` or `LIVE_VIEWS`. `_VIEW_WHEN`
gains its entry, which `view_catalog` requires anyway (`catalog.py:100`):

> `status`: What this service allows and how busy it is: version, profile, limits (deadline
> ceiling, longest key ttl), free slots, live runs, the environments in use and who holds them.
> One row; no params.

**Where it is built.** New module `trestle/server/status_view.py`, `build_status(control) ->
dict`. `ControlSurface.query` (`control.py:339-348`) answers `view == "status"` with it before
delegating to `Project.query`, because the row needs the scheduler, the registry and the build,
which the filesystem backend does not have. `ControlSurface` gains `version: str | None = None`,
set by `Kernel.start_service` from the `version` it already receives (`main.py:61`). That avoids
running `build_version()` (`trestle/common/build.py`, two git subprocesses for an editable
checkout) on every query. The envelope is `bounded_envelope(...)` (`views.py:176-194`) with one
row, `next_cursor` null and `truncated` false, checked by `validate_envelope` like every other
view. Params and cursor are ignored.

**Gating.** It is answered under both profiles, to any session, while draining too, with the same
fields. Nothing in it is session scoped. That is consistent with `recent_runs`, and with
`list_plugins`, which already returns the absolute `plugin_search_paths`.

**The row:**

```json
{
  "version": "0.6.0",
  "profile": "restricted",
  "draining": false,
  "home_format": 2,
  "servers": 1,
  "limits": {
    "deadline_default_s": 300,
    "deadline_ceiling_s": 3600,
    "finalization_margin_s": 35,
    "idempotency_ttl_s": 3600,
    "max_ttl_s": 604800,
    "max_running_runs": 31,
    "queue_depth": 256,
    "max_held_runs": 256
  },
  "slots": {"in_use": 2, "free": 29},
  "runs": {"queued": 0, "held": 1, "running": 2, "session_ended": 1},
  "environments": [
    {"env": "\"dev\"", "held_by": "r_…", "free_at": "2026-10-22T10:05:00Z", "runs": 2}
  ],
  "environments_truncated": false,
  "plugins": {"published": 7, "failed": 0},
  "plugin_dirs_missing": []
}
```

| Field | Source |
|---|---|
| `version` | `control.version`, else `trestle.__version__` |
| `profile` | `admission.profile.mode` |
| `draining` | this server's `scheduler.draining` |
| `home_format` | `home.home_format(home)` |
| `servers` | `len(home.live_servers(home))`: a count, never ids |
| `limits.*` | `load_config(home)` at each call (as admission reads it): `operator_limits.deadline_ceiling`, `max_ttl_s`, `idempotency_ttl_s`, `max_running_runs`; `clock.finalization_margin`; the 300 s default deadline; this server's `scheduler.queue_depth` and `max_held` |
| `slots` | `pools.read_sched(home)`: `in_use = len(state["running"])`, `free = max(0, max_running_runs - in_use)`, home wide |
| `runs` | the live markers (`live_run_ids`, `read_marker` `state`), home wide; `session_ended` via `SessionReader` over each live run's state.json |
| `environments` | `pools.key_runs(state)` grouped by key: `held_by` and `free_at` from `busy_holder` (exactly the refusal's values), `runs` = the key's running plus queued count. Soonest `free_at` first, **at most 8**, `env` cut to 96 bytes with `…` |
| `plugins` | `len(registry.snapshots)`; `failed` from a new one-line `Registry.failure_count()` (no import validation per call, as `catalog()` would do) |
| `plugin_dirs_missing` | watched folders that do not exist, **at most 4**, each cut to 200 bytes |

**Not in it, on purpose:** anything about credentials (identities, grants, store health; Decision
2: "nothing shows the store", even though change 3 put it in `trestle doctor`); server ids, pids
and ports; the admission lock holder; storage and retention; the allowlist itself (that is
`list_plugins`' `allowlisted`); session ids.

**Budget:** the whole serialized envelope is at most **4,096 bytes**. A unit test fills every
capped list at its cap with maximum-length values and asserts it. Worst case is about 3.4 KB.

---

## 2c: the `exec` plugin

### Where it comes from: seeded

The plugin is one file, `trestle/plugin/_guide/examples/exec.py`. It has two uses with no copy to
drift:

- the guide serves it as `trestle://guide/examples/exec`, and the guide's tests already check
  that every example publishes as is;
- `run_init` (`trestle/server/init_cmd.py:103-142`) writes it to `$TRESTLE_HOME/plugins/exec.py`
  when that file is missing. It reads the bytes from `resources.files("trestle.plugin._guide")`,
  the same way echo is seeded from `_SEED_ECHO`. `--no-seed` skips both. The output line becomes
  `seeded: echo.py, exec.py`.

Why seeded, and not built in or opt-in:

- **Built in** (always in every catalog) would change every catalog, `catalog_hint`'s
  empty-catalog case and many tests. It would also contradict "operator-published".
- **A seeded file** is the operator's: their `init` wrote it, it is in their watched folder,
  change 3 records `origin: operator`, and they can delete or edit it.
- **Opt-in** (copy it yourself) would leave the full profile with no one-off path by default,
  which sends agents back to their shell, the thing 2c exists to stop. Under the full profile
  `exec` adds no power: `publish_plugin` already runs any code as the user.

The cost: a home keeps the copy it was seeded with. An existing home gets `exec` from `trestle
init` (idempotent, it seeds only missing files) or by copying the example. `service upgrade` runs
`init --upgrade --no-seed` (`trestle/server/service.py:579`) and does not add it, and
`scripts/install.sh` runs plain `init` only on a first install. That is documented. Because
`trestle init` re-seeds a missing seed file, the documented off switch is leaving
`TRESTLE_EXEC_ROOTS` unset, not deleting the file (owner decision 4). Plugins may import only
`trestle.plugin` and `trestle.workflow` (guide topic `imports`), so the file is self-contained
(stdlib plus `trestle.plugin`), about 90 lines, within `EXAMPLE_BYTES` (6,144).

### Arguments and declaration

```python
@trestle(summary_fields=["ok", "exit_code", "timed_out", "error", "stderr_tail", "stdout_tail"])
def exec(ctx: Context, argv: list[str], cwd: str) -> ExecAnswer: ...
```

- `argv`: the command, a list of strings. There is no shell: `ctx.exec` refuses a string
  (`trestle/child/execproc.py:57-74`). `argv[0]` may be any program, a shell included. Nothing
  interposes one, and no denylist pretends to stop `sh -c`.
- `cwd`: required, absolute. There is no default: a one-off command's directory is the most common
  mistake, so it is stated.
- **No `timeout`, `env` or `input` arguments.** The run's deadline is the timeout: send
  `run(..., deadline_s=N)` up to the ceiling. The default is 300 s, and the plugin declares no
  deadline of its own. Settings come from the operator's `[plugins.env]`. Fewer arguments mean a
  smaller surface to document and test.
- No `secrets=`, no `credentials=`, no `env_arg` (one-off commands are not serialized by
  environment), not `repeatable`.
- `ExecAnswer` is a `TypedDict` in the file: `ok: bool`, `exit_code: int | None`,
  `timed_out: bool`, `duration_s: float`, `stdout_tail: str`, `stderr_tail: str`,
  `truncated: bool`, `output: str`, `error: str | None`. A function named `exec` shadows the
  builtin in its module, which is allowed, and the repo's ruff selection (`E,F,I,UP,B`) does not
  flag it.

### cwd under declared roots

- **Roots** come from the plugin's environment, `TRESTLE_EXEC_ROOTS`: absolute paths joined by
  `os.pathsep`, set by the operator in `config.toml`:

  ```toml
  [plugins.env]
  TRESTLE_EXEC_ROOTS = "/Users/me/src/app:/Users/me/src/lib"
  ```

  `[plugins.env]` is the existing operator-only channel to plugin runs. It is read again at each
  run's start (`conductor.py:205`), so no restart is needed. This adds no config schema and no
  kernel code.
- **Roots unset or empty, or any root not absolute:** `raise Blocked("exec_roots_unset", "The
  operator lists the directories exec may run in: TRESTLE_EXEC_ROOTS under [plugins.env] in
  config.toml, absolute paths joined by ':'.")`. The answer reads `blocked`, code
  `plugin.exec_roots_unset`, and the agent reports and stops.
- **Rule:** `real = os.path.realpath(cwd)`, then `real` must be an existing directory, and for some
  root `r`, `os.path.commonpath([real, os.path.realpath(r)]) == os.path.realpath(r)`. Symlinks are
  resolved first, so a link inside a root that points outside it is refused. The command runs with
  `cwd=real`. This rule confines where the command starts, not what it touches: it is argv run as
  the user.

### Running it

- `timeout = ctx.remaining() - RETURN_MARGIN_S` (5 s), so on a timeout the plugin still has time
  to return its answer before the deadline kill. A plain plugin with no `stop_grace` is killed at
  the deadline. If that leaves 0 or less, the answer is the error `no_time_left` and nothing
  starts.
- `ctx.exec(argv, cwd=real, timeout=timeout, max_output_bytes=MAX_OUTPUT_BYTES)`, with the
  default `output="exec.log"` and no `env` and no `credentials`. `MAX_OUTPUT_BYTES` is 16 MiB per
  stream. `ctx.exec` keeps the **first** `max_output_bytes` of each stream (`execproc._Capture`,
  `execproc.py:31-55`), so a large cap makes truncation rare. When it happens, `truncated` says
  so. This is a thin wrapper: `ctx.exec` owns the process group, the kill on timeout or stop, the
  capture and the artifact (`trestle/child/context.py:176-226`).
- The command's environment is the plugin's: the service's `PATH`, `HOME` and `TRESTLE_HOME`,
  plus `[plugins.env]`. The child pops `TRESTLE_RUN_SECRETS` before plugin code is imported, and
  `exec` declares no credential, so it holds no value.

### The answer and the artifact

| Field | Value |
|---|---|
| `ok` | `result.ok` (it exited 0) and no `error` |
| `exit_code` | `result.returncode` (null on a timeout, a stop or an error) |
| `timed_out` | `result.timed_out` |
| `duration_s` | `result.duration_s` |
| `stdout_tail`, `stderr_tail` | the last 1,500 bytes of the kept text of each stream, cut on a UTF-8 boundary and, when the cut is mid-line and a newline exists, from the next line |
| `truncated` | `result.truncated`: a stream passed 16 MiB, so the tail is the end of what was kept, not of the stream |
| `output` | `result.artifact`: the `art_…` handle of the kept output, or `""` when there was none |
| `error` | null, or `<code>: <detail>` with the detail cut to 300 characters |

The `error` codes are all returned, not raised, so the run ends `succeeded`, `answer.outcome`
reads `passed`, and the agent reads `summary.ok`. This is the same contract as `repo_tests`'
`ok: false`.

| Code | When |
|---|---|
| `cwd_not_absolute` | `cwd` is relative |
| `cwd_not_a_directory` | missing, or not a directory |
| `cwd_outside_roots` | not under any root; the detail lists the roots (operator configuration, not a secret) |
| `program_not_found` | `FileNotFoundError` from `ctx.exec`; the detail says the service's `PATH` is not your shell's |
| `not_executable` | `PermissionError` |
| `argv_invalid` | `TypeError` from `ctx.exec` (an empty list) |
| `no_time_left` | less than the return margin left |
| `operator_command` | `os.path.basename(argv[0])` is `trestle`: the operator's CLI. This steers an agent and is not a boundary, since `sh -c` gets past it (owner decision 3) |

The summary fits the default 4,096-byte budget: two 1,500-byte tails plus about 300 bytes of
fields. `summary_fields` orders the fields so `ok`, `exit_code` and `error` are kept first.

### The restricted-profile default: off unless listed

The orchestrator leans towards off. This design agrees, and argues it on stronger grounds than taste.

- A restricted service exists to narrow what agents may do. `exec` is "any command as the user",
  which is the opposite. Adding it implicitly would silently turn every restricted service into a
  shell.
- **With change 3, `exec` reaches the credential store.** The store's key lives in
  `credentials/store.key` (the file provider) or in the Keychain, created through
  `/usr/bin/security` (change 3, "The store"). A command run through `exec` can read either:
  `cat` for the file, and `security find-generic-password` for the Keychain item. Change 3
  creates that item with no `-T`, so it trusts `/usr/bin/security`, the same program the service
  reads it through unattended, and no prompt stops another process of the user's. The two
  providers are equally exposed. A live credentialed run's values are also in its spawn
  environment, where `ps -E` shows them (owner decisions, problem A). The record's honest limit
  says "agents cannot see the store" holds *inside* Trestle. `exec` moves the agent's shell inside
  Trestle, so on a host that sandboxes the agent's own shell it would be the only hole. "No
  credential grant ever" does not close it: no grant is needed to read the store's files.

So it is off unless listed. The allowlist is already explicit, so this means only that Trestle
never adds `exec` to it. Listing it is warned about, never refused (owner decision 1): a refusal
keyed on a name is bypassed by renaming the file, and no cheap middle option confines a read. One
function, `exec_exposure_warning`, produces the line when the profile is restricted, `exec` is on
the allowlist and `credentials.toml` defines any identity. `trestle doctor` lists it among its
warnings, and `trestle credentials add` and `grant` print it: `exec is allowlisted under the
restricted profile and this home holds N credential identities: any agent on this service can run
commands as you, and so read every credential value (the store under either key provider, and a
live run's environment)`. `security.md` says the same (2d).

### Never a credential

- **At publication:** in `declared_from_function` (`trestle/server/plugin_schema.py`, where change
  3 parses `credentials=` with `_declared_credentials`), a function named `exec` with a non-empty
  `credentials` raises `DeclarationError("@trestle: the exec plugin never carries a credential;
  remove credentials=")`. That is the existing path: `publish_plugin` answers
  `publication.validation_failed` (`registry.py:429-438`), and a folder file is listed in
  `list_plugins`' `failed` (`registry.py:236-243`). There is no new code. The name is the
  constant `NEVER_GRANTED = frozenset({"exec"})` in change 3's `trestle/common/credentials.py`,
  shared with the CLI.
- **At grant:** `trestle credentials grant <name> exec` and `add … --grant exec`
  (`trestle/server/credentials_cli.py`, change 3) refuse with the exit code and form that command
  uses for its other refusals (for example, granting to an agent-published plugin), before
  anything is written.
- The rule keys on the well-known name agents are told to use. An operator who copies the file
  under another name has written their own plugin, and grants are theirs to give.

---

## 2d: skill and agent docs

### The rule

Agent-facing text never tells the agent to run `trestle`, or any other command that belongs to the
service or its operator (an install into the service's Python), whether in its own shell, through
`exec`, or any other way. Nor does it tell the agent to do work a plugin covers in its own shell. A
command **addressed to the operator** may be quoted, only in the form `the operator runs:
<command>`, so the agent can relay it word for word and stop. The record's "nothing agent-facing
mentions the CLI" is amended to this (owner decision 3).

Today's text does not all meet this yet. Change 3's `credential_not_granted` and
`credential_unavailable` use the form, but `credential_unknown` says `the operator adds it:`, and
`credential_unavailable` embeds a store detail that names the key's location and can push the
operator clause past the 200-character cut. The packs `catalog_hint` says `Install with: pip
install …` without saying who should run it (`plugin_paths.py:14-17`), and the empty-catalog hint
tells the agent to write a file into a plugin folder (`:9-12`). The fixes are under "Other files"
and in owner decision 3.

Most of "report it and stop" already landed with change 2. `skills/trestle/SKILL.md:50` and `:61-63`,
and `docs/agents.md:22-34`, say it, and the line "fall back to running the command by hand" is
gone. What remains: the new surface is not named anywhere. `docs/agent-console-mcp.md` §0 still
documents stdio-era `command` host configs and CLI bootstrap (`:12-104`), which change 1a should
have removed. `.cursor/skills/trestle/SKILL.md:45-51` tells the agent to run `trestle init && trestle
doctor`. A few rows name the CLI as the agent's fix.

Agent-facing files: `skills/trestle/SKILL.md`, `.cursor/skills/trestle/SKILL.md`,
`docs/agents.md`, `docs/agent-console-mcp.md`, `trestle/plugin/_guide/**`, the view catalog text
and `authoring.server_instructions`. The installed copy `~/.claude/skills/trestle/SKILL.md` is
byte-identical to the repo's today, and `docs/install.md:148-150` tells the operator to copy it.
The release notes say to copy it again.

### `skills/trestle/SKILL.md`

- **Ground rule 1**, replace `:61-63`:

  > 1. **MCP only.** Use the ten Trestle tools. Never run `trestle`, or the work a plugin does, in
  >    your own shell. If the tools are missing or the service does not answer (connection
  >    refused, HTTP 401, every call failing the same way), say what you saw and stop: starting,
  >    stopping and configuring the service is the operator's job. A message that says what "the
  >    operator runs" is for the operator: report it word for word and stop. Never run it yourself,
  >    through `exec` or any other way.

- **Ground rule 3**, replace:

  > 3. **Refusals are not runs.** An `admission.*` or `publication.*` answer has no `run_id`.
  >    `retryable: false`: fix the request (the message says how) or report it. `retryable: true`:
  >    wait `retry_after_s` seconds, then resend. `admission.environment_busy` also names the run
  >    holding the environment (`held_by`, free by `free_at`): send yours with
  >    `after={"run": "<held_by>", "when": "ended"}` instead of retrying.

- **New ground rule 5:**

  > 5. **Ask the service.** `query(view="status")` (no params) says what this service allows and
  >    how busy it is: version, profile, deadline ceiling, longest key ttl, free slots, live runs,
  >    and the environments in use with who holds them.

- **New section "One-off commands: `exec`"**, after "Running tests":

  > When no plugin owns the work and you need one command (a `git status`, a version check), run it
  > through the `exec` plugin, never your shell:
  >
  > ```
  > run(plugin="exec", args={"argv": ["git", "status", "--short"], "cwd": "<absolute dir>"},
  >     wait_ms=1000, completion="terminal")
  > ```
  >
  > - `argv` is a list and no shell is added: no pipes, globs or `&&`.
  > - `cwd` must be under a directory the operator lists; outside it, `error` says
  >   `cwd_outside_roots` and names them.
  > - Read `summary.ok`, `exit_code`, `stderr_tail` and `stdout_tail` (the last 1,500 bytes of
  >   each). `output` is the whole kept output: `fetch` it with `tail` or `grep`.
  > - Give it a deadline that fits: `run(..., deadline_s=600)`.
  > - If `exec` is not in `list_plugins`, or is `allowlisted: false`, Trestle does not run one-off
  >   commands on this service. Say so. Whether you may run it yourself is your host's rule, not
  >   Trestle's.
  > - A command you run again and again should be a plugin: propose one.

- **New section "When your session ends"**, after "When a run ends `blocked`":

  > Runs belong to the service, not to your session. Closing the chat, a reconnect or a host
  > restart never stops one. Find it again by its key (`query(view="run_by_key")`, or
  > `await_runs(run_ids=[], keys=[...])`). A live run whose session has ended shows
  > `session_ended: true` in `query(view="recent_runs")` and `query(view="run")`. Cancel a run you
  > did not send only when it blocks you (it is the `held_by` of your refusal) and its
  > `session_ended` is true. Never cancel one as cleanup.

- **"When stuck"**: replace the `environment_busy` row and add these rows:

  | Symptom | Next step |
  |---|---|
  | `admission.environment_busy` | send yours with `after={"run": held_by, "when": "ended"}`; or, if `query(view="run", params={"run_id": held_by})` shows `session_ended: true` and it blocks you, `cancel(held_by)` |
  | `admission.queue_full`, `admission.home_busy` | wait `retry_after_s`, resend; again and again: report it |
  | `admission.service_draining` | the service is stopping or restarting: wait `retry_after_s`; if it then does not answer, report it and stop |
  | `admission.not_allowlisted` | `list_plugins` marks `allowlisted`; use one that is, or report it |
  | `admission.budget_does_not_fit`, `admission.ttl_out_of_range` | `query(view="status")` has `deadline_ceiling_s` and `max_ttl_s`; resend inside them |
  | `admission.credential_*` | the operator must act: report the message and stop |
  | `projection.not_owner` | another session's run, and that session is still open: leave it |

### `.cursor/skills/trestle/SKILL.md`

- **Rule 8** (`:25`), replace:

  > 8. **MCP only**: never run `trestle`, or the work a plugin does, in your own shell. If the
  >    tools are missing or the service does not answer, say so and stop; the service is the
  >    operator's. A message naming what "the operator runs" is for the operator: report it.
  >    Never run it yourself, through `exec` or any other way.

- Add **rule 9**: `query(view="status")` for limits and busyness. Add **rule 10**: a one-off
  command is `run(plugin="exec", args={"argv": [...], "cwd": "<abs>"})` when `list_plugins` has it.
- Replace **"Bootstrap (if catalog empty)"** (`:45-51`) with: `An empty catalog carries a
  catalog_hint on list_plugins: report it to the operator, or publish_plugin under the full
  profile.`
- In the **"When stuck"** row `plugin_not_found` (`:76`), replace "`trestle init` or `publish_plugin`" with
  "report it to the operator, or `publish_plugin`". Add the `environment_busy`,
  `retry_after_s`, `not_allowlisted` and `not_owner` rows from above, shortened. In the
  `import_failed` row (`:78`), replace "pack plugins need `pip install -e ".[packs]"`" with "report
  it: the operator installs the packs".
- **Delete "Verify"** (`:85-91`, the `python scripts/...` smoke commands). These are developer
  checks, and the skill is agent-facing.

### `docs/agents.md`

- **§1 The service** (`:22-34`): after the "say so plainly … and stop" paragraph, add: `A refusal
  or hint that says what "the operator runs" is for the operator: relay it, do not run it.` And
  `query(view="status")` answers what this service allows: version, profile, limits, slots, live
  runs and the environments in use.
- **§3 Verify**: add `query(view="status")` answers, with `draining: false`.
- **Restricted profile** (`:112-121`): replace the cancel sentence with `cancel works on runs this
  MCP session started, and on any run whose session has ended (session_ended: true in recent_runs
  and run)`. Replace the `not_owner` row with `cancel for a run another session started while that
  session is still open | Leave it; the run continues. Once its session has ended you may cancel
  it if it blocks you.` Add: `list_plugins marks each plugin allowlisted: true or false.`
- **New section "When your session ends"**, after "Cancel, deadlines and cleanup". It uses the
  skill's wording, plus the definition: `A session ends when your host deletes it, or after 30
  minutes with no request in flight (the host has gone). A dropped connection is not an end.`
- **New section "One-off commands: exec"**, after "Run capacity". It uses the skill's text, plus
  the answer table from 2c and `exec is seeded by the operator's trestle init; under the restricted
  profile it runs only if the operator lists it`.
- **Retrieval table**: add the row `What does this service allow / how busy is it? | query |
  status (no params)`. Add `status` to the named-views list (`:168`).
- **Refusal rows.** The trees table's `environment_busy` row (`:562`): `Fix: send with after={"run":
  held_by, "when": "ended"}, or resend after retry_after_s`. The "Common mistakes" row `home_busy`
  (`:594`): `Retry after retry_after_s (2 s)`, dropping "`trestle doctor` names the holder". The
  "Empty catalog" row (`:590`): `report it to the operator, or publish_plugin (full profile)`.
  The "Pack plugin `valid: false`" row (`:592`): `report it: the operator installs the packs;
  catalog_hint says how`, in place of the bare `pip install -e ".[packs]"`.
- **Run capacity** (`:333`): `and the operator's trestle doctor warns when it is set`. Add one
  line: `query(view="status") shows slots in use and free, home wide`.
- **Operator vs agent** (`:633`): add `cancel` to the operator CLI list.

### `docs/agent-console-mcp.md`

- **Header** (`:3`, `:8`): "using `trestle serve`" becomes "through the service's MCP endpoint".
  The second sentence becomes `Agents reach run evidence only through MCP. Operators use the CLI
  and trestle ops serve (operator-sessions-telemetry.md); nothing in this playbook runs trestle.`
- **§0, replaced whole** (`:12-104`):

  > ## 0. Attach
  >
  > The operator runs Trestle as a per-user service and wires each host once
  > ([`install.md`](install.md)): the URL `http://127.0.0.1:18732/mcp` with `Authorization:
  > Bearer <agent token>`. Nothing in this playbook runs `trestle`.
  >
  > ### Verify attach
  >
  > 1. The host shows ten tools (nine under the restricted profile: no `publish_plugin`).
  > 2. `query(view="status")` answers with the service's `version`, `profile` and `draining: false`.
  > 3. `list_plugins` lists at least `echo`. An empty catalog carries a `catalog_hint`: report it
  >    to the operator.
  >
  > If the tools are missing or the endpoint does not answer, report it and stop.

- **§3 "Admission refusals"** (`:253-255`): add a paragraph on `retryable`, `retry_after_s`, and
  `held_by`/`free_at`, with the refusal example from 2b.
- **§5 Named views**: add `status`. Add `session_ended` to the `recent_runs` and `run` row lists.
- **§8** (`:361-372`): delete the `pip install -e ".[dev]"` and `python scripts/smoke_agent_mcp.py`
  block. These are developer checks, like the Cursor skill's "Verify" section (owner decision 3).
  Replace "Manual MCP check: start `trestle serve`, …" with "Manual MCP check: `list_plugins` →
  `run(echo)` → `fetch(...)`".
- **§9** (`:380-382`): "MCP ten tools (`trestle serve`)" becomes "(the service's endpoint)". Add
  `cancel` to the operator CLI row.

### Other files

- `docs/security.md`:
  - The profiles table's `cancel` row (`:90`): `only runs received in the same MCP session, and
    runs whose session has ended (deleted by the client, or expired after 30 minutes with no
    request in flight); any other is refused projection.not_owner and keeps running`.
  - A new short section **"Sessions"**: what an end is, the per-server file, that the mark is
    information and cancel scope only, and that no session id reaches an agent.
  - A new paragraph **"The exec plugin"**: it runs any program as your user. Under the restricted
    profile it runs only when listed. Listing it gives every agent on the service what your user
    can do. That includes reading every credential value: the store's key under either key
    provider (the key file, or the Keychain item through `security`, which needs no prompt), and a
    live credentialed run's environment. It also includes every `[plugins.env]` value (an agent
    can run `env`). Do not list it on a service that holds credentials unless your agents already
    have an unsandboxed shell; `trestle doctor` warns about it. Keep secrets out of
    `[plugins.env]`. Under the full profile, `exec` is a shell for every agent that holds the token,
    and a host's per-command approval (a shell permission prompt, a sandbox) does not see it: the
    host approves the `run` call, not its argv. That is already true of `publish_plugin`. To keep
    `exec` off, leave `TRESTLE_EXEC_ROOTS` unset (owner decision 4).
  - "Operator vs agent" (`:98-102`): add `cancel`.
- `docs/plugins.md`: a short section **"The exec plugin"**: what it is, the roots, the answer, and
  that a credential declaration on a plugin named `exec` is refused at publication.
- `docs/install.md` (on top of the owner's pending edits and change 3's):
  - §3: `trestle init seeds exec.py; set TRESTLE_EXEC_ROOTS under [plugins.env]`, and on an
    existing home, `trestle init` adds it. The off switch is leaving `TRESTLE_EXEC_ROOTS` unset,
    because `trestle init` puts back a deleted seed file.
  - Troubleshooting: add a row `An agent's run outlived its session and holds an environment |
    trestle cancel <run_id>; trestle doctor lists session_ended_runs`, and a row `exec answers
    blocked: exec_roots_unset | set TRESTLE_EXEC_ROOTS`.
  - "The home": mention `servers/<id>.sessions.json`.
- `README.md` "Operators (CLI + HTTP)" (`:98-105`): add `trestle cancel <run_id>`.
- `trestle/plugin/_guide/root.md`: one line, `Before a run: query(view="status") says what this
  service allows`. Keep it within `INDEX_BYTES` (1,536).
- `trestle/plugin/_guide/topics/exec.md`: one last line, `For a one-off command without writing a
  plugin, agents run the exec plugin (trestle://guide/examples/exec).` Keep it within
  `TOPIC_BYTES`.
- `trestle/server/authoring.py`, `server_instructions` (`:54-66`): add the sentence `For what this
  service allows, query view status.` That is enough for an agent that never reads the skill.
- `trestle/query/catalog.py`: the `status` entry, plus a `session_ended` mention in `recent_runs`
  and `run`.
- `trestle/server/plugin_paths.py` (`:9-17`): the packs hint quotes the operator's install command
  in the `the operator runs:` form. The empty-catalog hint no longer tells the agent to write a
  file into a plugin folder: `No plugins published: the operator adds plugin files to the
  service's plugin folders; under the full profile, publish_plugin adds one.` (owner decision 3)
- Change 3's messages in `trestle/server/credentials.py`: `credential_unknown` uses `the operator
  runs:`, `credential_unavailable` drops the store detail, and the 200-character cut never removes
  the operator clause (owner decision 3).

---

## Interaction with change 3

Change 3 is built first, as 0.5.0, and this bundle branches from it. These are the files both
touch, and how they meet. None of it needs a design change. Each is a merge in a different hunk
or a call into change 3's code.

| File | Change 3 | This bundle |
|---|---|---|
| `trestle/server/admission.py` | grant check in `admit` before import validation; `credential_resolve` after the plan; a `credential_values` parameter on `_admit_locked` and `write_admitted_run` | 2b edits `_environment_busy` only (`:391-422`). 2a adds nothing here: `caller_session` reaches state.json through `ledger.state_record` |
| `trestle/common/codes.py` | three `admission.credential_*` codes after `ADMISSION_AFTER_UNKNOWN` | `RETRY_AFTER_S`, `DEFAULT_RETRY_AFTER_S` at the end; the credential codes are not in it (not retryable) |
| `trestle/common/types.py` | `DeclaredMetadata.credentials` | `RequestOutcome` fields; `PluginCatalogRow.allowlisted` |
| `trestle/server/main.py` | `credentials=load_credentials(...)` on `Admission` in `create_kernel` | `allowlist` on `Project` in `create_kernel` (adjacent hunk); `sessions` in `start_service`; `SessionWatch` in `_http_server` |
| `trestle/server/doctor.py` | `credentials_report`, `DoctorReport.credentials`, the store's health | `session_ended_runs` section; `exec_exposure_warning`, which reads `load_credentials(home).identities` and the profile (owner decision 1) |
| `trestle/cli.py` | `add_credentials_parser(sub)` and its dispatch | the `cancel` subparser and dispatch |
| `trestle/server/plugin_schema.py` | `_declared_credentials` in `declared_from_function` | the `exec` refusal right after it |
| `trestle/common/credentials.py` (new in 3) | name rules | `NEVER_GRANTED` |
| `trestle/server/credentials_cli.py` (new in 3) | `grant`, `add --grant` | refuse `exec`; `add` and `grant` print `exec_exposure_warning` (owner decision 1) |
| `trestle/server/credentials.py` (new in 3) | `refusal`, `resolve` and their messages | `credential_unknown` in the `the operator runs:` form; `credential_unavailable` without the store detail; a cut that keeps the operator clause (owner decision 3) |
| `trestle/server/plugin_paths.py` | none | both catalog hints reworded (owner decision 3) |
| `docs/agents.md` | three refusal rows ("Report it and stop") | the same table and sections (2d) |
| `docs/security.md`, `docs/plugins.md`, `docs/install.md`, the record | credentials sections | 2d edits and amendments |
| `trestle/plugin/_guide/topics/exec.md`, `index.md` | credentials lines | one line in `exec.md`; the example list grows by itself |
| `tests/proof/divergence.toml` | entries for the new codes | entries listed under Tests |

How 2b's refusal fields apply to the credential refusals: they don't. All three are
`retryable: false` and need the operator, so they carry no `retry_after_s`, `held_by` or `free_at`.
The `status` view lists no identity, grant or store state, and `describe_plugin` keeps change 3's
names-only `credentials` field. `exec` can never declare one. The doctor warning and
`security.md` cover the one real interaction: `exec` reaching the store, and a live run's
environment (owner decisions, problem A).

---

## Tests

Each row of the record's acceptance column has tests. "MCP" means driven only through the proof
host's ten tools, with no reads of the home. Every bound comes from `tests.proof.tolerances` or
`trestle.common.clock`.

### Proof host changes (`tests/proof/mcp_host.py`)

- `sever("connection_drop", req_id=...)`: `_exchange` keeps each request's `HTTPConnection` by id.
  This mode shuts down and closes that socket. The exchange then answers the held request with its
  usual "connection lost" error. No DELETE and no notification are sent.
- `peer() -> McpHost`: a second client of the same server process, sharing `proc`, `port`, `token`
  and `home`, with its own `initialize`, session id, request ids and queues. A peer's `close()`
  sends DELETE and never kills the process. Today `new_session()` (`:328`) replaces the one
  session, so two sessions cannot run at once.
- A test-local fixture `tests/single/control/mcp/fixtures/env_hold.py`:
  `@trestle(deadline=120, env_arg="env") def env_hold(ctx, env: str, seconds: float = 60.0)`,
  looping on `ctx.cancelled`. Tests copy it into the host's plugins folder. It stays out of
  `tests/fixtures/plugins`, which every `McpHost` seeds, so no catalog assertion elsewhere moves.

### 2a: "A run outlives its session untouched and is marked; another agent session or the operator can end it; `on_session_lost="cancel"` is possible to build on it"

| Test | Kind | Shows |
|---|---|---|
| `tests/single/control/mcp/test_session_loss.py::test_deleted_session_leaves_the_run_and_marks_it` | MCP | session A runs `slow` (8 s, `wait_ms=0`) and DELETEs. Peer B sees `session_ended: true` on the `recent_runs` row and on `query(view="run")` while the run is live. B's `await_runs` returns `succeeded`, `answer.outcome` `passed`, `answer.root_stop` null. Once the run has ended, the row reads `session_ended: null` |
| `::test_connection_drop_is_not_a_session_end` | MCP | A holds a terminal `run`, then `connection_drop`. B sees `session_ended: false` while the run is live, and the run ends `succeeded` |
| `::test_dropped_terminal_wait[connection_drop\|session_close\|cancel_notification]` | MCP | the record's unverified case: a held `run(completion="terminal", idempotency_key=K)` of `slow` (6 s) is severed mid-wait. B's `await_runs` gets `succeeded`/`passed`, no stop, `error` null. `run_by_key` K has one row, `joinable` true. Re-sending K from B returns that run's terminal answer, and only one run exists |
| `::test_restricted_cancel_follows_the_session` | MCP | restricted, allowlist `slow`. B's `cancel` of A's run is `projection.not_owner`. A DELETEs; B's `cancel` is `projection.cancel_accepted`; `await_runs` shows `cancelled` |
| `::test_full_profile_cancel_unchanged` | MCP | full profile: B cancels A's live run at once |
| `tests/core/sessions/test_cancel_cli.py` | CLI + MCP | a run started over MCP; `trestle cancel <id> --home` exits 0 with `cancel accepted for <id>`; the run ends `cancelled`. Again: exit 1, "not cancellable". An unknown id: exit 1. A child handle: exit 1, naming the root |
| `tests/core/sessions/test_doctor_sessions.py` | CLI + MCP | after a DELETE with a live run, `trestle doctor` prints `session_ended_runs: 1` and the run line |
| `tests/core/sessions/test_mcp_sessions.py` | unit, fake monotonic clock | idle bound plus guard, minus 1 s: not ended; plus 1 s: `expired`. An open GET (in flight) survives the sweep. DELETE 2xx: `deleted` written before the forward. 404 on a known sid: `expired`; on an unknown sid: nothing. Prune keeps an entry while an owned live run names it and drops it after `ENDED_KEEP_S` otherwise. A dead owner reads `server_gone`. `live_servers(prune=True)` removes the file. `end()` is idempotent, and is called once per session with its reason: the seam |
| `::test_sdk_idle_timeout_pin` | unit | runs `mcp.http_app(transport="streamable-http")`'s lifespan (Starlette `TestClient`), finds the `/mcp` route's `session_manager`, and asserts `session_idle_timeout == DEFAULT_SESSION_IDLE_TIMEOUT == SDK_IDLE_TIMEOUT_S`. It fails if fastmcp starts passing its own value or the SDK changes its default |

### 2b: "Every row of the gap table that names a view or a field has it, and a test reads each one through MCP alone"

All in `tests/single/control/mcp/test_agent_surface.py` (MCP):

| Gap-table row | Test |
|---|---|
| `environment_busy`: `held_by`, `free_at` | A runs `env_hold(env="dev")`, `wait_ms=0`. B sends the same with `deadline_s=30` and gets `admission.environment_busy`, `retryable` true, `held_by` = A's run, `free_at` parseable and in the future, `retry_after_s` an int of at least 1. B resends with `after={"run": held_by, "when": "ended"}` and the run is `held` |
| `queue_full`, `home_busy`, `service_draining`: `retry_after_s` | `queue_full`: `config.toml` `max_running_runs = 1`, `queue_depth = 0`; a second `slow` gets `retry_after_s == 5`. `home_busy`: the test process holds `locks/admission.lock` across the call (`home.try_lock`), giving `retry_after_s == 2`. `service_draining`: a live `slow` (20 s), then SIGTERM to the server; `run` gives `retry_after_s == 10`, and `status` shows `draining: true` |
| `not_allowlisted`: `allowlisted` | restricted, allowlist `["echo"]`: `echo` is true and `slow` is false. Full profile: no `allowlisted` key |
| `budget_does_not_fit`, `ttl_out_of_range`: the ceilings | `config.toml` `[operator] deadline_ceiling_s = 1800`, `[keys] max_ttl_s = 3600`: `status.limits` carries both |
| what only doctor shows: `status` | every key of the row; `profile`, `draining`, `home_format == 2`, `servers == 1`, `plugins.published` equal to `list_plugins` count, `slots.in_use` while a run is live; the restricted variant reads `"restricted"`; serialized size ≤ 4,096 |
| an orphan holds an environment | restricted, allowlist `env_hold`: A holds `dev`; B is refused with `held_by`; `query(view="run", params={"run_id": held_by})` shows `session_ended: false`; A DELETEs; it now shows `true`; B `cancel(held_by)` is accepted; B's resend is admitted |

And units in `tests/core/surface/`:

- `test_refusal_fields.py`: every code in `RETRY_AFTER_S` round-trips through `to_dict` as an int;
  a retryable outcome with an unknown code gets `DEFAULT_RETRY_AFTER_S`; a non-retryable one has
  none of the three fields. A source scan of `trestle/` finds each `retryable=True` site's code in
  `RETRY_AFTER_S`. The `retry_after_s` arithmetic for `environment_busy` uses a fixed `now`, and
  `busy_holder` agrees with `busy_until` (including ties).
- `test_status_view.py`: the 4 KiB budget at every cap, with the env cut to 96 bytes and the paths
  cut to 200.

### 2c: "A one-off command runs through `run(plugin="exec")` with a bounded answer; a credential declaration on it is refused at publication"

| Test | Kind | Shows |
|---|---|---|
| `tests/single/control/mcp/test_exec_plugin.py::test_exec_runs_a_command` | MCP | the example copied in, `[plugins.env] TRESTLE_EXEC_ROOTS` set to a temp dir. `argv = [sys.executable, "-c", "print('x' * 100000); import sys; sys.exit(3)"]` gives `ok` false, `exit_code` 3, a 1,500-byte `stdout_tail` ending in `x`, `summary` not truncated, and `output` fetchable with `tail` |
| `::test_exec_timeout_returns_an_answer` | MCP | `deadline_s=15`, a 60 s sleep: `timed_out` true, `exit_code` null, run `succeeded` |
| `::test_exec_restricted_needs_listing` | MCP | restricted, no `exec` in the allowlist: `allowlisted` false, `run` refused `admission.not_allowlisted` |
| `::test_exec_credentials_refused_at_publication` | MCP | `publish_plugin` of the example plus `credentials=["x"]` gives `publication.validation_failed` with the message. The same file dropped in a folder is a `failed` row |
| `tests/core/exec_plugin/test_exec_plugin.py` | unit (`trestle.plugin.testing.call_plugin`, real `FakeContext.exec`) | the ok path; tails on UTF-8 and line boundaries; `cwd_not_absolute`, `cwd_not_a_directory`, `cwd_outside_roots`, a symlink escape; roots unset or relative raise `Blocked("exec_roots_unset")`; `program_not_found`; `argv_invalid`; `no_time_left` with a short `FakeContext` deadline; `operator_command` for `argv[0]` `trestle` and `/abs/path/trestle` |
| `tests/core/exec_plugin/test_seed.py` | CLI | `trestle init` writes `plugins/exec.py` byte-equal to the guide example and leaves an existing one alone; `--no-seed` writes neither |
| `tests/core/credentials/test_cli.py` (change 3's file) | CLI | `trestle credentials grant x exec` and `add x --env V --grant exec` are refused before anything is written |
| `tests/core/exec_plugin/test_doctor_warning.py` | CLI | restricted profile, `exec` allowlisted, one identity configured: doctor and `trestle credentials add` print the warning. No warning without identities, under the full profile, or when restricted without `exec` listed |
| `tests/test_plugin_authoring.py` | existing | add `"exec"` to `EXAMPLE_NAMES`; the size and publish-as-is tests cover it |

### 2d: "An agent following the skill alone never runs a shell command for work Trestle does"

`tests/core/docs/test_agent_docs_mcp_only.py`, over the agent-facing files listed in 2d:

- No line in a fenced code block starts with `trestle ` or `$ trestle`.
- No `"command":` key appears in a JSON block.
- No "fall back" appears.
- Every line that names `` `trestle <service|init|doctor|recover|cancel|credentials|serve|pin|unpin|ops>`` or `pip install` also contains "operator".
- A companion unit test, `tests/core/surface/test_operator_quotes.py`, covers the messages built at
  run time: the credential refusals (with 64-character names) and the packs `catalog_hint` each
  contain `the operator runs: ` with the command whole after the 200-character cut (owner
  decision 3).
- `skills/trestle/SKILL.md` names `view="status"`, `retry_after_s`, `held_by`, `plugin="exec"` and
  `session_ended`.

### Goldens and divergence entries (`tests/proof/divergence.toml`, all `direction = "additive"`)

- `ADD-tools-list-query-view-status`: `items = [{ path = "$.tools[3].inputSchema.properties.view.enum", value = "status" }]`.
- `ADD-query-view-status` (facet `query_row_shapes`): `status` in `$.view_names`, the key
  `$.views.status`. The facet's `NO_RUN_VIEWS` (`tests/pins/d_evidence/facets.py:28`) gains
  `"status"`.
- `ADD-recent-runs-session-ended`, `ADD-run-row-session-ended` (facet `query_row_shapes`):
  `declared_fields` items, and the `observed.*.row_shape.session_ended` keys.
- `ADD-outcome-retry-after-s`, `ADD-outcome-held-by`, `ADD-outcome-free-at` (facet
  `refusal_codes`): items on `$.outcome_fields`. `refusal_frame` (a `plugin_not_found` refusal) is
  unchanged, because the fields are absent when not retryable.
- There are no new codes, no `runview_shape` change, no `spec_keys` change, no `context_surface`
  change and no ledger kind.
- Tests that assert state.json's exact keys (look in `tests/core/shared_home/`) gain
  `caller_session`.

---

## Commit order and size

The bundle is one branch from 0.5.0 and one release, 0.6.0. Commits are in dependency order, each
green on its own. Sizes are rough, in source lines and test lines.

| # | Commit | Source | Tests |
|---|---|---|---|
| 1 | **Refusal fields.** `RequestOutcome` fields, `RETRY_AFTER_S`, `busy_holder`, `_environment_busy` with `held_by`, `free_at` and the computed `retry_after_s`; divergence entries; unit tests | ~90 | ~120 |
| 2 | **Session ends.** `mcp_sessions.py` (watch, middleware, file, reader), `state_record.caller_session`, `start_service` and `_http_server` wiring, prune in `home.py`, `session_ended` on `recent_runs` and `run`, the `_in_scope` predicate; divergence; unit tests and the MCP tests for marking and the restricted cancel | ~330 | ~450 |
| 3 | **Operator side of 2a.** `trestle cancel`, doctor's `session_ended_runs`; CLI tests | ~90 | ~120 |
| 4 | **The dropped terminal wait.** Proof host `connection_drop` and `peer()`; the parametrized test | ~60 (harness) | ~150 |
| 5 | **Status and `allowlisted`.** `status_view.py`, `ControlSurface.version` and the query intercept, `Project.allowlist`, catalog entry, `Registry.failure_count`; divergence; the gap-table MCP tests | ~200 | ~300 |
| 6 | **`exec`.** Guide example, init seed, the publication refusal, the grant refusal, `operator_command`, `exec_exposure_warning` in doctor and the credentials CLI; tests | ~150 | ~270 |
| 7 | **Docs, skills and agent-facing messages.** Every 2d edit, change 3's three messages, the two catalog hints, the doc test and the message test | ~400 doc lines, ~20 source | ~100 |
| 8 | **Record and release.** The amendments below, `__version__` and `pyproject.toml` `version = "0.6.0"`, release notes (including "copy the skill again") | ~80 | none |

Total is about 900 lines of source, 1,450 of tests and 400 of docs. Commits 2 and 4 can swap.
Commit 5 comes after 2 because `status` counts `session_ended` runs. Follow the repo's release flow
(fence fragments, gates, the light release flow in use since 0.3.2).

---

## Findings on the record

These are wrong, underspecified or risky. Each comes with what this design does.

1. **"Record the end of a streamable-HTTP session" assumes there is an event to record.** There is
   none in fastmcp 3.4.7 or mcp 1.30.0. A disconnect is not an end, and a killed host is seen only
   by the SDK's 30-minute idle expiry. This design mirrors the rule at the ASGI layer (2a).
   *Amend* the record with the definition and the latency.
2. **"Under one service, draining means a restart is coming and 'wait' is the whole answer"** (gap
   table) is wrong for `trestle service stop` and `uninstall`. Those drain and then stay down
   (`stop` also disables the unit, "As implemented"). *Amend:* "draining means the service is
   stopping; it comes back after `restart` or `upgrade`, not after `stop`. Wait `retry_after_s`;
   if it then does not answer, report it and stop."
3. **"Nothing agent-facing mentions the CLI" (2d) is contradicted by change 3**, whose refusal
   messages say "the operator runs: trestle credentials …", and by `catalog_hint`'s `pip install`.
   Those messages are right to name the fix. *Amend* the rule to: "never tells the agent to run it;
   may quote what the operator runs". Owner question 3, decided: yes, in the one form `the
   operator runs: <command>`, which `credential_unknown` and both catalog hints do not use yet.
4. **Change 1b's row says `ctx.exec` returns "a tail in the result".** The code keeps the first
   `max_output_bytes` of each stream (`execproc._Capture`). For `exec`, a truncated stream's tail
   is the end of the kept head. This design raises the cap to 16 MiB and reports `truncated`.
   *Recommend* a later small change that makes `ctx.exec` keep the last bytes too (a head-and-tail
   capture). *Amend* the 1b row to describe what is shipped.
5. **The `exec` plugin plus change 3's store breaks "agents cannot see the store" inside Trestle**
   for any service that lists it under the restricted profile (2c). "Never a credential grant" does
   not cover it. That is why the default is off, plus the doctor warning and the `security.md`
   text. Owner question 1 asked whether to refuse rather than warn. Decided: warn.
6. **`exec` makes `[plugins.env]` readable by agents** (`env` prints it). The record presents
   `[plugins.env]` as the place for operator settings. *Amend* `security.md` and `install.md`: no
   secrets there; identities belong in `credentials.toml`.
7. **"A run whose session has ended is cancellable by any session" has a sharp edge.** A host that
   keeps no GET stream open and idles 30 minutes between turns loses its session, and with it,
   under the restricted profile, the protection of its runs against other agents. It also gains
   the ability to cancel its own pre-expiry runs, which today it cannot. The design keeps the
   record's rule. The skill confines cross-session cancel to a `held_by` blocker whose
   `session_ended` is true.
8. **`held_by`, "the blocking run id", is ambiguous** when several runs queue on one environment.
   This design defines it as the run whose recorded deadline is `free_at` (the last in line), which
   is what makes `after=` correct.
9. **"`retry_after_s` on every retryable refusal"** is listed beside admission codes only, but
   `projection.not_finalized`, `projection.cursor_expired` and `publication.registry_conflict` are
   retryable too. This design covers all of them, from one table.
10. **The gap table says `status` shows "what only `trestle doctor` shows"**, and change 3 added
    credentials to doctor. Decision 2 wins: `status` shows nothing about the store. *Amend* the row.
11. **"Unverified: … a dropped `terminal` wait"** is mostly verified already by
    `tests/single/control/mcp/test_sever_rejoin.py` (cancel notification, DELETE, SIGKILL). The
    connection drop and the run that ends on its own are added here. *Amend* the paragraph to name
    the tests.
12. **Change 1a's acceptance ("no doc tells a host to spawn `trestle serve`") is not met:**
    `docs/agent-console-mcp.md:60-95` still shows a stdio `command` config. 2d rewrites it.
13. **The build reports 0.3.1.** `trestle/__init__.py` and `pyproject.toml` say `0.3.1` while the
    tags are at v0.4.1. The installed service prints `trestle 0.3.1+gfc4999d0dc64.dirty`, and
    change 3's branch has not bumped it either. The `status` view's `version` would mislead. Commit
    8 sets 0.6.0 in both places. Check that 0.5.0 sets 0.5.0.
14. **`[credentials.<name>]` in `config.toml`.** Change 3 moved the tables to `credentials.toml` and
    amends the record itself. This is noted only because 2c's doctor warning reads that file.

---

## Open questions for the owner

Only decisions the record leaves to you, each with a recommendation. All four are decided in
"Owner decisions (2026-10-22)" at the end; where a decision differs from a recommendation here,
the decision holds.

1. **`exec` allowlisted on a restricted service that holds credentials: warn or refuse?** Refusing
   (the server will not start with that combination) is a real control for hosts that sandbox the
   agent's shell. It is theater for hosts that give agents a shell anyway.
   **Recommendation: warn in 0.6.0** (doctor, plus `security.md`). Revisit with change 4, whose
   Keychain and 1Password backends can make the store's unlock a prompt the agent cannot answer.
2. **The session idle timeout: keep the SDK's 30 minutes?** fastmcp 3.4.7 offers no setting. A
   shorter one would mean reaching into the session manager after its lifespan starts. Shorter
   gives faster orphan visibility. It also means more reconnects for hosts that idle without a GET
   stream, and earlier loss of restricted-cancel protection for their runs.
   **Recommendation: keep 30 minutes in 0.6.0.** Decide it inside the `on_session_lost` change,
   which is the one that needs it short.
3. **Amend 2d to "agent-facing text may quote a command addressed to the operator, never tell the
   agent to run one"?** Change 3's refusals and `catalog_hint` already do this, and stripping
   them would make an operator guess the fix. **Recommendation: yes**, enforced by the doc test.
4. **Seed `exec` with `trestle init`, so it is present by default under the full profile on new
   homes?** The alternative is opt-in (the operator copies it). Under the full profile it adds
   nothing `publish_plugin` does not already allow, and without it an agent has no one-off path.
   **Recommendation: seed it.** It stays off under the restricted profile unless listed.

---

## Record amendments to make when this lands

In `docs/decisions/service-and-credentials.md`:

- **Status line:** changes 2a, 2b, 2c and 2d implemented (0.6.0).
- **Decision 1, "When the agent's session ends"**: gap 1 gets the session-end definition (deleted,
  expired after 30 minutes idle or a 404; not a dropped connection; `server_gone` computed). Gap 3
  stays "its own change", with the note that it depends on the idle timeout. Replace the
  "Unverified" paragraph with the tests that verify it (`test_sever_rejoin.py`,
  `test_session_loss.py::test_dropped_terminal_wait`).
- **The gap table:** the draining sentence (finding 2); "what only doctor shows" excludes anything
  about credentials (finding 10).
- **Sequence row 1b:** `ctx.exec` keeps the first `max_output_bytes` of each stream (finding 4).
- **Sequence row 2d:** "nothing agent-facing mentions the CLI" becomes: "agent-facing text never
  tells the agent to run a command that belongs to the service or its operator, in its shell,
  through `exec` or otherwise; such a command is quoted only as `the operator runs: <command>`"
  (owner decision 3).
- **New subsection "As implemented (changes 2a to 2d)"**, one bullet each:
  - the ASGI session watch and why not `_discard_session`;
  - `servers/<id>.sessions.json` and its pruning, no ledger row, nothing at restart;
  - `session_ended` true, false or null, on `recent_runs` and `run`, and in doctor;
  - the `_in_scope` predicate for cancel and child views;
  - `trestle cancel` exit codes;
  - `held_by` as the latest-deadline run and `free_at` in ISO;
  - `retry_after_s` on every retryable outcome, from `codes.RETRY_AFTER_S`, with the values;
  - the `status` row, its 4 KiB budget and what it leaves out;
  - `allowlisted` under the restricted profile only;
  - `exec` as a seeded guide example, its `TRESTLE_EXEC_ROOTS`, its answer, and the 16 MiB cap;
  - `exec` off under the restricted profile unless listed, with the doctor warning;
  - the publication refusal through `publication.validation_failed` and the grant refusal;
  - `on_session_lost` not in 0.6.0, and its seam.
- **"What this record does not decide":** remove the `exec` restricted-default item (decided: off
  unless listed). Add "the session idle timeout (the SDK's 30 minutes is in force; settle it with
  `on_session_lost`)" and "a head-and-tail capture for `ctx.exec`". Also add "an unlock agents
  cannot perform: a separately signed service binary named by the Keychain ACL, and handing run
  secrets to the child by something other than the environment; until both exist, any process of
  the user's can reach the values" (owner decision 1, problem A).
- **`docs/security.md` "Related"**: change "credentials are not yet" to reflect 0.5.0 and 0.6.0.

### Deviations made while implementing (2026-10-22)

Each is the smallest change the code forced, also in the record's "As implemented (changes 2a to
2d)".

1. **Release 0.5.1, not 0.6.0** (orchestrator). Wherever this design says 0.6.0, read 0.5.1.
2. **`server_gone` reads the run's own owner lock** where the reader has the run's directory
   (the cancel predicate, the `run` and `recent_runs` rows, `status`, doctor), and the owner's
   server lock only otherwise. A kernel that admits runs without serving (the CLI, and every
   in-process test kernel) holds owner locks but no server lock, so the server lock alone read
   every such run as `server_gone` and put it in every session's scope. The owner lock is what
   the reaper reads, so the two never disagree about a dead server.
3. **A session first seen on a request is held unconfirmed.** "`request_started` on an unknown
   sid adds it" and "a 404 on an unknown sid writes nothing" conflict; the watch adds such a sid
   unconfirmed, confirms it on an answer below 400, forgets it on a 404, and `end` acts only for
   a confirmed one (an `initialize` answer confirms at once).
4. **The `refusal_codes` divergence entries carry `items` on `$.outcome_fields`.** The differ
   refused every selector on that facet (a new code is named by `codes`); it now admits items on
   the outcome's field list and nothing else (`tests/proof/differ.py`).
5. **`status` has 12 keys**, as its example row shows; "13" above is a miscount.
6. **`busy_until` is gone** (owner policy 2026-10-22: no compatibility path kept only for
   continuity). `busy_holder` is the one function; `free_at` is its deadline.
7. **`run_init(seed_echo=)` is `run_init(seed=)`**, since it now seeds two files.
8. **The packs hint quotes** `~/.trestle-venv/bin/pip install -e ".[packs]"` (run from the
   checkout), the service venv's install command in `install.md`.
9. **`TRESTLE_EXEC_ROOTS` is split on `os.pathsep`** (`:` on the supported platforms).
10. **`exec`'s `cwd_outside_roots` detail names the roots first**, so the 300-character cut keeps
    them.
11. **Host-approval wording:** `security.md` says "a confined shell" where this design says
    "a sandbox", because `tests/core/admission/test_cl_a2_profile.py` refuses any mention of a
    sandbox that does not disclaim one.

---

## Owner decisions (2026-10-22)

The owner delegated the four open questions above. Each is decided here, after checking the
design's reasoning against master `fc4999d0` and change 3 at `820b5f78`. The `b2c64bd0` that the
header cites is no longer on the `credentials-store` branch, which was rewritten into `dcbd1857`
and `820b5f78`, so change 3 line numbers in this section are for `820b5f78`. Where a decision
differs from a recommendation above, the decision holds.

### 1. `exec` allowlisted on a restricted service that holds credentials: warn, never refuse

**Decision.** Warn, in `trestle doctor`, in the output of `trestle credentials add` and `grant`,
and in `security.md`. The server never refuses to start over it, admission never refuses `exec`
over it, and no middle option is added.

**Reason.** No cheap refusal is a real control. The Keychain provider is no narrower than the key
file: change 3 creates the item with `security add-generic-password` and no `-T`
(`KeychainKeyProvider.create`), so it trusts `/usr/bin/security`, the same program the service
reads it through unattended, and any process of the user's can read it with no prompt (change 3's
`security.md` says so); a credentialed run's values are also in its spawn environment, where
`ps -E` shows them (problem A below). A refusal keyed on the name `exec` is bypassed by renaming
the file or by any allowlisted plugin that runs an argv the agent picks, and confining `cwd` or
dropping `TRESTLE_HOME` from the environment confines no read. A start refusal would take down
every unattended run (a restart loop under `KeepAlive = {SuccessfulExit = false}`) to block a
combination the operator chose by listing it.

**The implementer must:**

- Write one function, `exec_exposure_warning(home, profile) -> str | None`, in
  `trestle/server/doctor.py`. It returns the line when the profile is restricted, `exec` is on the
  allowlist and `credentials.toml` defines at least one identity, and None otherwise. Doctor
  appends the line to `warnings`. `trestle credentials add` and `grant` print it after their own
  output, because that is the one moment the operator is certain to be present. Both read the files
  as they are now (`load_config`, `load_credentials`), as doctor does.
- Word it: `exec is allowlisted under the restricted profile and this home holds N credential
  identities: any agent on this service can run commands as you, and so read every credential
  value (the store under either key provider, and a live run's environment)`.
- Add no rule about `$TRESTLE_HOME` to `TRESTLE_EXEC_ROOTS`, and strip nothing from exec's
  environment. Both would look like a control and confine nothing. That is the kind of denylist
  the design already refuses for `sh -c`.
- Drop the plan to revisit this with change 4. Its Keychain and 1Password backends do not change
  it: a store the service unlocks unattended as the user can be unlocked by any other process of
  the user's, and after the unlock the values travel in the run's environment anyway. Closing it
  needs a separately signed service binary that the Keychain ACL names, plus a way of handing
  values to the child that does not use the environment. Neither is planned. The record lists it
  under "What this record does not decide".
- Test it in `tests/core/exec_plugin/test_doctor_warning.py`: the warning from doctor and from
  `credentials add`; no warning without identities, under the full profile, or under the
  restricted profile without `exec` listed.

### 2. The session idle timeout: keep the SDK's 30 minutes

**Decision.** Keep 30 minutes in 0.6.0. The `on_session_lost` change decides whether to shorten it.

**Reason.** Nothing in 0.6.0 needs a shorter timeout: the mark is information and cancel scope,
and `on_session_lost`, the one feature that does need one, is out. Shortening it means setting
`session_idle_timeout` on the manager that fastmcp builds inside its lifespan closure
(`fastmcp/server/http.py:658`; `FastMCPStreamableHTTPSessionManager.__init__` does not take the
argument), which ties Trestle to fastmcp's internals. It would also make the 404-then-initialize
path routine for any host that idles without a GET stream, a path unverified for Claude Code,
Cursor and Claude Desktop, and lift the restricted-cancel protection from those hosts' runs
sooner.

**The implementer must** do nothing beyond the design: import `SDK_IDLE_TIMEOUT_S`, keep the pin
test, and write "30 minutes" in the docs. Before the `on_session_lost` change shortens anything, it
must check how each of those hosts answers a 404 on its session.

### 3. Operator commands in agent-facing text: quoted for relay, never run

**Decision.** Yes, amended to one fixed form. Agent-facing text never tells the agent to run a
command that belongs to the service or its operator (`trestle`, an install into the service's
Python), whether in its own shell, through `exec`, or any other way. A command the operator must
run is quoted only as `the operator runs: <command>`. The agent reports it word for word and stops.

**Reason.** Naming the exact fix saves the operator a guess, and change 3's refusals depend on it.
But the design overstated what already follows the rule: `credential_unknown` says `the operator
adds it:` (`credentials.py:299-302`, change 3), the packs `catalog_hint` says `Install with: pip
install …` without saying who should run it (`plugin_paths.py:14-17`), `docs/agents.md:592` and
`.cursor/skills/trestle/SKILL.md:78` give the agent `pip install` as its own fix, and
`credential_unavailable` can lose its operator clause to the 200-character cut (problem B). `exec`
is what makes one fixed form matter: an eager agent that reads `the operator runs: trestle
credentials grant gh release` can now run it in one call, and re-pin a grant to code no operator
has reviewed.

**The implementer must:**

- Change 3's messages (`trestle/server/credentials.py`): `credential_unknown` ends `the operator
  runs: trestle credentials add <name> --env VAR`. `credential_unavailable` drops the `StoreError`
  detail and reads `credential '<name>' could not be read from the store; the operator runs:
  trestle doctor`, because doctor already prints the detail. When `_outcome` cuts a message to 200
  characters, it shortens the descriptive part and never the `the operator runs:` clause.
- `trestle/server/plugin_paths.py`: `CATALOG_HINT_PACKS_MISSING` says the packs are missing from
  the service's Python and quotes the operator's install command in the fixed form.
  `CATALOG_HINT_EMPTY` stops telling the agent to write a file into a plugin folder. That folder is
  the operator's, and change 3 records a file there as `origin: operator`. It becomes: `No plugins
  published: the operator adds plugin files to the service's plugin folders; under the full
  profile, publish_plugin adds one.` If a golden pins either string, add an additive divergence
  entry.
- End both skills' "MCP only" rule with `Never run it yourself, through exec or any other way.`
- `docs/agents.md:592` and `.cursor/skills/trestle/SKILL.md:78`: the fix becomes `report it: the
  operator installs the packs`. Delete the `pip install -e ".[dev]"` and `python
  scripts/smoke_agent_mcp.py` block in `docs/agent-console-mcp.md` §8 (`:361-369`). These are
  developer checks, like the Cursor skill's "Verify" section, which 2d already deletes.
- `exec` returns the error `operator_command` when `os.path.basename(argv[0]) == "trestle"`:
  `trestle is the operator's CLI; report the command to the operator instead`. It steers an eager
  agent and is not a boundary (`sh -c` gets past it). `plugins.md` says so.
- Tests: the doc test's check that a line naming a command also contains `operator` covers
  `pip install` as well as the `trestle` subcommands. A unit test,
  `tests/core/surface/test_operator_quotes.py`, builds the three credential refusals with
  64-character names, plus the packs hint, and asserts that each contains `the operator runs: `
  with its command whole. The exec unit test adds `operator_command`.

### 4. Seed `exec` with `trestle init`: yes

**Decision.** Seed it, on new homes and on any home where `trestle init` finds it missing. An
upgrade never adds it. Under the restricted profile it stays off unless listed.

**Reason.** Under the full profile `exec` adds no power: `publish_plugin` already runs any code as
the user. While `TRESTLE_EXEC_ROOTS` is unset, `exec` answers `blocked` and names the one step the
operator must take, so a new home gets a path that keeps an agent's one-off commands in Trestle's
ledger, not a shell that is open by default. Existing homes are left alone: `service upgrade` runs
`init --upgrade --no-seed` (`service.py:579`), and `scripts/install.sh` runs plain `init` only on a
first install. That is right, because an upgrade should never widen what an agent can run.

**The implementer must:**

- Have `init` write no roots: which directories agents may run commands in is the operator's
  choice. After `seeded: echo.py, exec.py`, `init` prints `exec: set TRESTLE_EXEC_ROOTS under
  [plugins.env] in config.toml; until then it answers blocked`.
- Document the off switch as leaving `TRESTLE_EXEC_ROOTS` unset (or, under the restricted profile,
  not listing `exec`), not as deleting the file. `trestle init` re-seeds a missing seed file, as it
  re-seeds `echo.py` today (`init_cmd.py:129-132`).
- Add the full-profile case to the `security.md` paragraph "The exec plugin" (2d). `exec` is a
  shell for every agent that holds the token, and a host's per-command approval (a shell permission
  prompt, a sandbox) does not see it, because the host approves the `run` call, not its argv. That
  is already true of `publish_plugin`; `exec` makes it one call away. An operator who relies on host
  approvals leaves the roots unset.
- Say in the release notes that an existing home gets `exec` from `trestle init`, which adds only
  missing files.

### Problems these decisions exposed

A. **Run secrets can be read from the process table** (change 3, and `secrets=` since
   MC-CORE-13). At spawn, the conductor passes a run's secret values, credentials included, to the
   wrapper as plain JSON in `TRESTLE_RUN_SECRETS` (`conductor.py:207-209` and
   `redact.encode_env`, change 3), and the wrapper passes its environment on to the child
   (`wrapper/spawn.py`). The child's `take_env` pops the variable from `os.environ`, but a
   process's initial environment does not change, and `ps -E -ww -p <pid>` shows it, for the
   wrapper and the child alike, to any process of the same user. This was checked on this machine: `ps -E` still printed a variable that a
   Python process had popped. So for the life of a credentialed run, an unsandboxed agent shell, a
   full-profile agent's plugin, or `exec` can read the values without touching the store, whatever
   the key provider. Change 3's `security.md` ("a variable the child removes before the plugin is
   imported") overstates the protection. This bundle does not fix it. Change 3 should pass the
   values over an inherited pipe instead (the wrapper's stdin is `DEVNULL` today), or state the
   exposure in `security.md` and in the record's honest limit.
B. **`credential_unavailable` shows the store to the agent** (change 3, `credentials.py:324-329`).
   Its message embeds the `StoreError`, which names the key provider together with the Keychain
   service and account, or the key file's path: exactly the lookup an agent would need. That
   contradicts Decision 2 ("nothing shows the store"). With a long home path, the 200-character
   cut also removes `the operator runs: trestle doctor`. Decision 3 fixes it.
C. **The design's "revisit with change 4"** (open question 1) assumed change 4 could make the
   unlock a prompt the agent cannot answer. A prompt would also stop unattended runs, which come
   first. Decision 1 drops the revisit.
D. **`exec` puts the operator's commands one call away from the agent.** The design's rule covered
   only the agent's own shell. Decision 3 extends it to `exec` and adds the `operator_command`
   steer.
E. **Under the full profile, `exec` bypasses host approvals** (decision 4). The draft `security.md`
   paragraph covered only the restricted profile.

### Edits made elsewhere in this design

- Header **Status** line: notes these decisions.
- **Summary** item 8: the fixed phrase and the message test.
- **2a, `on_session_lost`**: the idle-timeout bullet points to decision 2.
- **2c, "Where it comes from: seeded"**: the off switch, and the install script.
- **2c, "The answer and the artifact"**: the `operator_command` row.
- **2c, "The restricted-profile default"**: the second bullet (both key providers, the run
  environment) and the warning paragraph (its wording, where it prints, no middle option).
- **2d, "The rule"**: rewritten to decision 3, and the `catalog_hint` claim corrected.
- **2d, the skills**: ground rule 1 and Cursor rule 8 end with the never-run sentence; the Cursor
  `import_failed` row.
- **2d, `docs/agents.md`**: the packs row (`:592`).
- **2d, `docs/agent-console-mcp.md`** §8: the developer block goes.
- **2d, "Other files"**: the `security.md` exec paragraph (both key providers, the run environment,
  the full-profile case), the `install.md` off switch, the two catalog hints, and change 3's
  messages.
- **Interaction with change 3**: the `doctor.py` and `credentials_cli.py` rows, new rows for
  `credentials.py` and `plugin_paths.py`, and the closing paragraph's "one real interaction".
- **Tests**: the 2c exec unit and doctor-warning rows; the 2d doc test and the new message test.
- **Commit order and size**: rows 6 and 7.
- **Findings on the record** 3 and 5: point to the decisions.
- **Open questions for the owner**: a line saying they are decided here.
- **Record amendments**: the wording of the 2d row; "What this record does not decide" gains the
  signed binary and the non-environment hand-off.
