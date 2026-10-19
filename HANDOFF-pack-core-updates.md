# Handoff: build the pack-driven core updates and ship everything as one commit

## In plain words

Two finished pieces of work are waiting on local branches. A list of nine smaller improvements is agreed but not started. Your job is to combine the finished work, build the nine, and put all of it on the shared repository as one commit.

What we learned: the people writing Trestle packs keep rebuilding the same risky plumbing by hand:
- running shell commands with no time limit;
- waiting in loops;
- passing settings to scripts;
- logging from several threads at once, which can corrupt a run's event log.

Core can give them a few safe, small building blocks instead. A second review cut the original list of ten ideas down. Several were already handled, wrongly sized, or against earlier decisions. The owner then removed two more and postponed one.

What the results are:
- **Already finished:**
  - live progress for long runs, on the branch `live-run-visibility`;
  - running Trestle as a background service with a login token, on the branch `service-mode`.
  - Both pass their full test suites, apart from one per-commit environment check.
- **Not started:** the agreed list of nine improvements, plus one large item at the end that waits for the owner's say-so.

What that represents: one release. Packs get safe ways to run commands, report "I'm blocked, a person must do X", get their settings, and be tested without a server. All of today's work reaches the shared repository in a single clean commit.

## Identity

Pack-driven core updates and the combined release for Trestle. Repo `~/projects/trestle` (Python, fastmcp 3.4.7). `origin/master` = `f97cc1cf`.

## Goals (cascade)

- **Parent SSOT:** `docs/proposals/2026-10-19-pack-core-updates-plan.md` (on branch `live-run-visibility`), headings `## Goal`, `## In scope, in order` and `## Delivery`. Its **Acceptance** lines are the done conditions for each item. `## Out of scope` is settled: do not reopen it.
- **Binding constraint:** `docs/decisions/service-and-credentials.md` (the `service-mode` branch's version is the newest): one per-user service, MCP only, agents never need a shell, agents name credentials, never values. Items R1, G2 and G3 amend this record; amend it, don't work around it.
- **Item detail:** `docs/proposals/2026-10-19-packs-recommendations-review.md`, under `## Per recommendation` and `## Gaps the report missed`. This is reference only; the plan decides scope.
- **This-node goal:** orchestrate the plan to completion. That means:
  1. integrate the finished branches;
  2. build items 1–9 with tests and docs;
  3. get the owner's go or no-go on item 10;
  4. run the full suite and the host-docker gate;
  5. then, with the owner's permission, put everything on `origin/master` as **one commit**.

  See `helper-goal-cascade` for the parent and this-node binding.

## Current state

- **Local commits not on origin:**
  - `master`: 3 commits on `f97cc1cf`, all decision-record commits (`c020ae14`, `05a56d87`, `6b32801f`).
  - `live-run-visibility` (worktree `~/projects/trestle/.claude/worktrees/live-run-visibility`), on master:
    - `bdd425f2`: the live-run code;
    - `174fcf55`: the pack analysis, its review and the old handoff;
    - the plan doc and this handoff, which are committed with it.
  - `service-mode`, on master:
    - `c76288a5`: bearer token, stdio removed;
    - `620f59f1`: `trestle service`;
    - `5c253e21`: docs;
    - `8a4b83d7`: drain fix.

    It also carries the decision-record changes.
  - Other local branches (`claude/xenodochial-gauss-3bbba3`, `claude/youthful-albattani-48edaf`) have nothing unique. `history` is an archive; leave it alone.
- **Uncommitted in the main checkout `~/projects/trestle`:**
  - `HANDOFF-live-runs.md`, an old brief: drop it, or ask.
  - `tests/proof/host/host-docker/01a094ba9b96f2781470297646f72fed8cbca7e4.json`, a host record for an old commit: probably obsolete, so ask before including it.
  - The `docs/decisions/service-and-credentials.md` edit. `service-mode` already carries a version of it; diff the two before discarding.
  - `.venv/`: never include.
- **Untracked in the live-run worktree:** `trestle-tests.toml`, a local test-runner pointer. Never commit it.
- **Test results so far:**
  - `live-run-visibility`: full suite 3,220 passed. The 3 failures caused by the change were fixed, and a 527-test re-run passed.
  - `service-mode`: 2,651 passed, by its session's report.
  - Both have one remaining failure, G-E2: it needs a host-docker record for the commit under test.
- **Verified facts (do not re-derive):**
  - **Venv:** the venvs (`~/projects/trestle-wt/.venv312`, `~/projects/trestle/.venv`) are editable installs pointing at the **main checkout**. Plugin children in tests import that copy unless `PYTHONPATH` names your worktree.
  - **Test runner:** `repo_tests` (`~/projects/ariadne/scripts/trestle/repo_tests.py`) scrubs `PYTHONPATH`; set its `python` in an uncommitted `trestle-tests.toml`. `repo_tests` runs on this repo share one lock (the git common dir), so another session's suite blocks yours. The full suite takes about 53 min.
  - **300 s client timeout:** the shared Trestle server still runs old code, so the Claude Code client drops a silent tool call after 300 s. Wait in `await_runs(timeout_ms≈285000)` loops, not `completion="terminal"`, until the service is restarted on the new code.
  - **Shell guard in worktree sessions:**
    - plain `git` and `git -C <main checkout>` are refused; use `/usr/bin/git` from inside your worktree;
    - multi-expression `sed`, `PYTHONPATH=… python` and long heredocs are refused;
    - put edit scripts in the scratchpad and run them, or use the Edit tool.
  - **Cancel:** a plain plugin is killed (SIGTERM to its tree) on the first poll after a cancel (`trestle/server/conductor.py:228-262`). The child has no SIGTERM handler. This is why G3 exists.
  - **Event-file corruption risk:** `append_ndjson` (`trestle/common/fsutil.py:52`) repairs the file's tail before each write, with no lock. Concurrent threads can corrupt `events.ndjson`, which is why R7b is first.

## Threads

1. **Integrate.** Cut `pack-core-updates` from local `master` in a new worktree. Merge `service-mode`, then `live-run-visibility`; both touch `trestle/server/main.py` (auth and transport, plus the progress relay). Resolve conflicts, then run ruff, mypy and the tests the merge touches.
2. **Items 1–9 of the plan, in order:**
   - 1 R7b lock: the lock must cover T3's `_attached` replace;
   - 2 R1 `ctx.exec` and `remaining()`: decision-record amendment first;
   - 3 G1 env table;
   - 4 R4a `Blocked`;
   - 5 G2: doc only;
   - 6 R6a warning;
   - 7 R3 `FakeContext`;
   - 8 T5 docs;
   - 9 G3: decision first.

   Items 5, 6 and 8 are independent and small. Items 1, 2 and 7 share `trestle/child/context.py` and the plugin surface; build them as one sequence.
3. **Item 10 (R5/T7, L):** ask the owner whether to build it in this release or split it out. Don't start it unasked.
4. **Verify:**
   - the full suite once, on the integrated tree;
   - then the host-docker gate for the final code commit:
     `python -m tests.proof.host.docker_gate run --select packages/trestle-packs/tests/test_docker_integration.py::test_stack_runner_live_compose`.
   - It runs once per sha, and never with `preflight` first.
5. **Ship as one commit** (see Constraints for the open mechanics):
   - build the single commit on top of `origin/master`;
   - confirm CI's G-E2 will accept its host record;
   - get the owner's explicit permission;
   - push;
   - then restart the shared service (`trestle service restart`) and check that a long `await_runs` over HTTP no longer drops.

## Constraints

- **One commit on origin.** The owner said on 2026-10-19: "all local changes, even the ones we just finished, will be merged to origin master in a single commit." Default: a fast-forward of `origin/master` by one squashed commit, with no force-push. If the host-record rule forces a second commit, or a parentless release squash, stop and ask.
  - A host record must name the commit it tested, so a single commit cannot carry its own record.
  - The archived-tip pattern is how earlier releases did it: a `Release-Of: <tip>` trailer, the tip pushed under `refs/archive/*`, and the record taken on the tip. Read `tests/pins/e_packs/test_g_e2.py` and the CI `release-graft` step before choosing.
  - The owner's memory "release-graft-local" describes both the archived-tip flow and the lighter v0.3.2+ flow.
- **Pushing to `origin` needs the owner's explicit permission**, each time. Never force-push or enable auto-merge without being told to. Expect the auto-mode classifier to deny force-pushes and tag batches; hand the owner the exact commands if so.
- **Before anything is public:**
  - The reports in `docs/proposals/` still name a company's internal modules (`hibob_project_up` and others); the author email and home paths are already redacted. Point this out before the push.
  - Never commit `trestle-tests.toml`, `.venv/` or files extracted from the pack zip.
- **Settled, do not reopen:** a shell command to start runs (T4), loading plugins from installed-package entry points, cancellable waits, the subprocess and sleep lint, status, attach-text and table, declared tool and env requirements, pack version display. `ctx.exec` is a plugin API, not a new MCP tool: the surface stays at ten tools. `config.toml` env values come only from the operator.
- AWS uses fakes or stubs; never configure real AWS credentials.
- Run pytest and long jobs through Trestle's `repo_tests` when present. Unattended completion comes before token cost: fall back to a direct run only when the shared lock would stall you, and say so.

## Next steps

1. Create the integration worktree and merge `service-mode` and `live-run-visibility` (thread 1).
2. Amend the decision record for R1, and write the G3 decision, as the first commits on the integration branch.
3. Build R7b, R1 and R3 as one sequence, with the acceptance tests from the plan.
4. Build G1 and R4a, then the small items G2, R6a and T5.
5. Ask the owner about item 10, and about the main-checkout leftovers (old handoff, old host record).
6. Run the full suite, then the host gate. Build the single commit, show the owner the exact push, and push on their yes.

## Open questions

- Should item 10 (multi-key locks) ship in this release or be split out?
- Is the single commit a fast-forward of `origin/master` (default), or a parentless release squash like v0.3.0? This depends on what G-E2 and the CI graft step accept.
- Should the report files in `docs/proposals/` that name internal company modules be published as they are, trimmed, or kept out of the commit?
- G3: should the clean stop be a child SIGTERM handler with a fixed grace, or a grace the plugin declares?

## References

- Plan (parent SSOT): `docs/proposals/2026-10-19-pack-core-updates-plan.md`
- Review (item detail): `docs/proposals/2026-10-19-packs-recommendations-review.md`
- Analysis: `docs/proposals/2026-10-19-packs-analysis.md`
- Decision record: `docs/decisions/service-and-credentials.md` (newest on `service-mode`)
- Plugin surface and context: `trestle/plugin/surface.py`, `trestle/child/context.py`, `trestle/common/fsutil.py`
- Publication checks: `trestle/child/validate.py`, `trestle/server/plugin_schema.py`
- Stop path: `trestle/server/conductor.py`
- Test runner: `~/projects/ariadne/scripts/trestle/repo_tests.py`
