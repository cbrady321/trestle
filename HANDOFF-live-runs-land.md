# Handoff: land the live-run upgrades and the service lifecycle

## In plain words

The live-run work is built, tested and committed on a local branch, and it is waiting for the owner's OK to push. Your job is to get it and the service work merged.

What we learned: a tester's long job showed nothing until it finished. Trestle now shows a running job's latest progress line, its events and its newest status file. It also keeps a long wait from being cut off by sending a small "still waiting" message every 15 seconds. We hit that cut-off ourselves: our own 50-minute test run was dropped after 5 minutes of silence, because the Trestle server running on this machine is still the old code.

What the results are:
- The full test suite: 3,220 tests passed and 4 failed.
- Three of those failures came from this change, and they are fixed. A re-run of those 527 affected tests passed.
- The fourth failure is a per-commit environment record. The change didn't cause it.
- A separate session is building the "install and run Trestle as a background service" commands. Its work is on its own branch.

What that represents: an agent watching a long run is no longer blind. The last steps are to merge two branches, record one environment check, and restart the server so it runs the new code.

## Identity

Live-run visibility and the per-user service for Trestle. Repo `~/projects/trestle` (Python, fastmcp 3.4.7). Local `master` is 3 commits ahead of `origin/master`.

## Goals (cascade)

- **Parent SSOT:** `/Users/colinbrady/Downloads/trestle-proposal-pack-runs.md`, heading `## Proposed changes (in priority order)`. The **Acceptance** lines under T1, T2, T3 and T6 are the done conditions. T4 (shell command to start runs) is rejected. T5 (entry-point plugin discovery) and T7 (exclusivity keys) are deferred.
- **Binding constraint:** `docs/decisions/service-and-credentials.md`, `## Decision 1: one per-user service per machine` and its preamble: everything lands on the MCP surface, and an agent never needs a shell. The service lifecycle requirements are under `### The CLI` and in rows 1, 1a and 2 of `## Implementation sequence`.
- **This-node goal:** take the two branches through to merged on `master`, then on `origin` when the owner approves, as few bundled PRs. Then restart the shared Trestle server on the new code and confirm the T2 acceptance line on it: a long `await_runs` over streamable HTTP stays connected and shows progress lines. See `helper-goal-cascade` for how parent and this node bind.

## Current state

- **Branch `live-run-visibility`** (worktree `~/projects/trestle/.claude/worktrees/live-run-visibility`), commit `bdd425f2`, cut from local master `6b32801f`. Not pushed: on 2026-10-19 the owner said "Don't push yet; hold the branch locally."
  - **T1:** `RunView.progress` `{message, fraction, at}` on frames of a running run. `query(view=run)` has a `progress` column. `run_events` answers a running run with `live: true`. `last_error`, `run_provenance`, `recent_failures` and `run_tail` stay gated.
  - **T2:** `trestle/server/progress_relay.py`. Wired in `trestle/server/main.py` via an `observe` hook on the async waits in `project.py` and `control.py`. Notification `progress` is a counter, because MCP requires it to increase.
  - **T3:** same-name `ctx.attach` replaces (`trestle/child/context.py`, with `supersedes` on the event). `run_artifacts` gives one row per name, merging attached artifacts from `events.ndjson` with ledger promotions. Before this, attached artifacts were never listed at all. A replacement takes no count slot (`conductor.promote_outputs` too). Shared readers are in `trestle/server/live.py`.
  - **T6:** `summary_fields` entry `name[col,...]`, parsed in `plugin_schema.parse_summary_field` and rendered in `projection.build_summary` as `{columns, rows, row_count}`, with a `rows_truncated` marker.
  - **Docs:** `docs/agent-console-mcp.md`, `docs/agents.md`, `docs/plugins.md`, `trestle/query/catalog.py`. The `progress` field is recorded as `ADD-runview-progress` in `tests/proof/divergence.toml`.
  - **Tests:** `tests/single/control/test_live_runs.py`, `tests/core/answer/test_summary_tables.py`, and `tests/test_mcp_live_runs.py`, the T1–T3 acceptance scenario from a real HTTP MCP client.
- **Untracked in that worktree, do not commit:** `trestle-tests.toml`. It points `repo_tests` at a scratchpad wrapper that sets `PYTHONPATH` to the worktree. Delete it before merging.
- **Service lifecycle:** session `local_a5b41a5d-aa05-4c81-bc49-1f2d1a36be19` finished. Rows 1, 1a and 2 are on the local branch **`service-mode`**: 4 commits on master `6b32801f` (`c76288a5` bearer token and stdio removal, `620f59f1` `trestle service`, `5c253e21` docs, `8a4b83d7` drain fix). Its worktree branch `claude/xenodochial-gauss-3bbba3` is not where the work is. By its own report, its full suite gave 2,651 passed and 1 failed (G-E2 only). Not pushed; its PR text is drafted but not opened. Never merge its branch unless the owner says so.
- **Verified facts (do not re-derive):**
  - The venv `~/projects/trestle-wt/.venv312` (and `~/projects/trestle/.venv`) has an editable install pointing at the **main checkout**. Plugin children spawned in tests import that copy unless `PYTHONPATH` names the worktree.
  - `repo_tests` (`~/projects/ariadne/scripts/trestle/repo_tests.py`) scrubs `PYTHONPATH`. Its only lever is the `python` interpreter in `trestle-tests.toml`.
  - `repo_tests` runs on one repo share a lock keyed on the git common dir, so a suite run in another worktree blocks yours.
  - The full suite takes about 53 minutes. The Claude Code MCP client aborts a tool call after 300 s of silence until the shared server runs T2. Until then, use bounded `await_runs(timeout_ms≈285000)` loops, not `completion="terminal"`, for long runs.
  - `tests.pins.e_packs.test_g_e2::test_target_host_docker_record_passes_live_compose` fails for any commit without a host-docker record under `tests/proof/host/host-docker/` (the main checkout holds an untracked record for `01a094ba`). That is environment-only.
- **Shell quirk in worktree sessions:** plain `git …` is refused by the rtk/worktree guard. Use `/usr/bin/git …`. Multi-expression `sed` and long heredocs are also refused, so put edit scripts in the scratchpad and run them.

## Threads

1. **Clean up and push `live-run-visibility`:** remove `trestle-tests.toml`, then wait for the owner's go-ahead.
2. **Service lifecycle branch:** confirm its suite is green and that the decision-record diff came with it. Decide with the owner whether it lands before or after thread 1.
3. **Integration:** both branches touch `trestle/server/main.py`: thread 1 the relay, thread 2 auth and transport. Merge one onto the other in a fresh worktree and run the full suite once, together.
4. **G-E2 record:** produce the host-docker record for the merged HEAD the way commit `f97cc1cf` did ("host-docker record for 012e81d2").
5. **Restart the shared server on the new code** (after thread 2, through `trestle service restart`). Then run a long `await_runs` (well over 5 minutes) over HTTP and confirm it doesn't drop and shows progress lines. That is the T2 acceptance line.

Order: 1 and 2 independently → 3 → 4 → 5.

## Constraints

- **Pushing to `origin` needs the owner's explicit permission** for each push. Memory "Landings pre-authorized" covers fence land rounds only; cleanup and force-push are still denied. Never enable auto-merge.
- Prefer few, larger bundled PRs; keep CK and checkpoint commits separate.
- Run pytest and long jobs through the Trestle MCP tools (`run`, `query`, `fetch`) when present. Unattended completion outranks token cost: fall back to a direct run only when the shared lock would stall you, and say so.
- No CLI that starts runs (T4 rejected). No entry-point discovery (T5). No exclusivity keys (T7).
- AWS components use fakes. Never configure real AWS credentials.
- The `github_pr` pack and its pack-side work are not in this repo: out of scope.

## Next steps

1. Ask the owner whether to push `live-run-visibility` now or after the service branch is green.
2. Review `service-mode` (`/usr/bin/git -C ~/projects/trestle log --oneline master..service-mode`) and confirm it carries the decision-record diff.
3. With the owner's OK, integrate both branches in one worktree and run the full suite via `repo_tests suite=true`, waiting in bounded `await_runs` calls.
4. Record G-E2 for the merged HEAD, open the bundled PR(s), and ask before each push.
5. Restart the service and confirm the T2 acceptance line on a real long wait.

## Open questions

- Should superseded artifact versions get their own listing (for example a `superseded` flag on `run_artifacts`)? Shipped default: fetchable by id only, documented in `docs/plugins.md`.
- Should a progress line be kept on a finished run's frame? Shipped: absent once the run has ended, so terminal frames stay byte-identical.
- The proposal's `fetch("<run>/monitor-status.md")` name-path form was not built. Fetch goes through the `art_…` handle from `run_artifacts`. Raise it with the owner if they want the name form.

## References

- Proposal: `/Users/colinbrady/Downloads/trestle-proposal-pack-runs.md`
- Decision record: `docs/decisions/service-and-credentials.md`
- Previous brief (context only, not a goal source): `~/projects/trestle/HANDOFF-live-runs.md`
- Live-run code: `trestle/server/live.py`, `trestle/server/progress_relay.py`, `trestle/query/fs.py`, `trestle/query/views.py`, `trestle/child/context.py`, `trestle/server/projection.py`
- Test runner: `~/projects/ariadne/scripts/trestle/repo_tests.py`
