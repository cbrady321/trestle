# Adversarial review: packs-analysis.md recommendations (section 3, T5/T7)

2026-10-19. Read-only. Core read on `live-run-visibility` (the worktree, master plus bdd425f2) and `master`; `service-mode` read with `git show`/`git diff`. Pack files (`zip:`) were read only. Nothing was run in the zip, and no git command touched it.

## Headline

The report reads the pack well, and its two central items hold up: `ctx.exec` (R1) and a plain-plugin `Blocked` (R4). Its biggest error is about the cancel model. For a plain plugin, cancel is not cooperative. The conductor sends SIGTERM to the run's process tree on the first poll after it sees the cancel flag, because a plain plugin's release slice is 0 (`trestle/server/conductor.py:232-262`; `docs/plugins.md:124-157`). The child installs no SIGTERM handler (grep finds none under `trestle/child/`). So `ctx.cancelled` is true for about 50 ms before the process dies. Three items rest on cooperative cancel: R2's cancellable waits, R1's cancel acceptance test, and F2 as a whole. They solve a problem core already solves by force, and they miss the real gap: a plain plugin gets no window in which to stop cleanly.

Four other items are mis-sized or contradict recorded design:

- **R5 (multi-key leases) is L, not M.** The lease is a single key in the `created` row, `sched.json` and the busy pre-check.
- **R7 contradicts B3-C3/C4.** `LocalProcessPort` refuses `DURABLE` by design.
- **R8 is mostly covered.** The `live-run-visibility` branch already covers it, and the 2 GB byte cap makes its motivating number irrelevant.
- **R6(c) already exists.** Core already has its mechanism, the `PORT_MODULES` → `env_arg` rule.

Three decision-record citations are wrong. The report missed two security-relevant gaps:

- Grants pin only the entry file's digest.
- Service mode has no way to give plugins an environment.

## Citation check

| Cited | Finding |
|---|---|
| `service-and-credentials.md:263` for `ctx.exec` | **Wrong.** Line 263 is the `credential_interactive` bullet. `ctx.exec` is at `:285` (SDK table) and `:460` (sequence step 5). The step-5 claim itself is correct. |
| `service-and-credentials.md:120` for PATH/HOME/TRESTLE_HOME | **Wrong line.** The text is at `:142` and `:372`. |
| `service-and-credentials.md:395` for `held_by`/`free_at` | **Wrong line.** The gap table row is `:420`. The fields are change **2b** (`:455`), already sequenced. |
| `docs/plugins.md:422-430` for `ctx.cancellation.wait_for` | **Wrong.** Lines 422-430 are the leaf-step table (`Blocked` row at `:430`). `wait_for` is at `:443-455`. |
| `plugin_schema.py:344` `DECLARED_KEYWORDS`; `admission.py:394-410,527-528` | Correct. |
| `child/context.py:92-100,167-169,192-195,269-271` | Correct, but **for the branch**: `_attached` and the T3 replace exist only on `live-run-visibility` (`context.py:98-100,167-169`). |
| `zip:core/artifacts.py:10-14`, `zip:chain/__init__.py:55-61`, `zip:app_up/ports.py:36-63`, `launcher.py:70-75`, `aws_preflight.py:108-112`, `github_pr/batch.py:50,318-324` | Correct. |
| F4: "packs must not depend on `trestle` (`zip:pyproject.toml:11-13`)" | **Overstated.** Those lines only list `pyyaml`; there is no stated prohibition. Master's pack already declares `dependencies = ["trestle"]` (`packages/trestle-packs/pyproject.toml`). |
| F10: monitor status re-attached "every round" | **Overstated.** `zip:github_pr/batch.py` `_report_monitor_progress` attaches only `if status != previous`. |
| F3: the pack's 35 s "copies `clock.finalization_margin`" | **Misread.** The finalization margin runs *after* the deadline (`plugins.md:146-149`). The pack subtracts it *before* the deadline, as its own reserve for writing reports. The number matches core's, but it means something different. |

## Per recommendation

### R1 `ctx.exec` (M)

- **Evidence.** Holds: 30 raw calls, few with timeouts, for example `zip:github_pr/open.py:98`.
- **Already done or decided?** Decided, not built. It is step 5 (`:460`), and the `exec` plugin is 2c (`:457`). The record says "none depends on a later one", so pulling it forward is allowed.
- **Design.**
  - The acceptance test "`sleep 999` cancelled at t+2 s returns within grace" already passes today with raw `subprocess`: the supervisor kills the whole tree on cancel (`conductor.py:249-262`). The real value is elsewhere: a per-call timeout that names the step, bounded capture, and the credential seam.
  - **Failure mode the report missed:** `output="artifact:<name>"` per call against `max_artifact_count = 1000` (`trestle/common/limits.py:17`). A 1 h `gh` poll every 15 s over 5 PRs is 1,200 calls. The default must be capture plus tail, with an artifact only on failure or above a size threshold, or under a reused name (T3 replace).
  - **"Allowlist plus `env`, never full `os.environ`"** is wrong today. The child's environment is already the service's (exactly PATH/HOME/TRESTLE_HOME under service mode). Stripping further breaks `docker` and `gh` with no way to supply values, because `config.toml` has no env table (gap G1). Inherit the child environment, apply overrides, and let the scrubber cover output.
  - It must be thread-safe (R7b), because the first consumer calls it from a thread pool.
  - "No shell to keep the no-shell posture" misframes the risk. The no-shell rule binds agents, not plugin code, which can call `subprocess` anyway (`service-and-credentials.md:349-353`). argv-only is still right, for quoting and scrubbing.
  - The `exec` plugin (2c) should be a thin wrapper over `ctx.exec`, not a second implementation.
  - Reserve `credentials=` now: refuse it until step 3 instead of ignoring it.
- **Size and rank.** M. Rank 2, after R7b.
- **Verdict: keep, reshaped.** Capture by default, the inherited environment, `credentials=` reserved, 2c built on top, and the acceptance test rewritten around a per-call timeout and the artifact count.

### R2 `wait_for`/`sleep`/`remaining` (S)

- **Evidence.** The sleeps exist. But a cancel produces SIGTERM within about 50 ms for a plain plugin (see the headline), so "a cancel during a 15 s poll stops the run in under 1 s" is already true by force.
- **Design.**
  - `ctx.remaining(margin="finalization")` encodes the wrong quantity. The finalization margin runs after the deadline, so the parameter should not exist.
  - `remaining()`, meaning deadline minus now, is a one-line convenience, useful as `ctx.exec`'s default timeout.
  - `wait_for_condition` already exists publicly (`trestle/workflow/values.py:87`).
- **Size.** S → XS.
- **Verdict: drop the waits; fold `remaining()` into R1.** The real need is G3, a stop window.

### R3 surface package, fake, `run_local` (M)

- **Already done?** Partly. Master's pack already depends on `trestle`, so `trestle.plugin.surface.Context` is importable. A separate `trestle-surface` distribution adds release overhead for a fork that should simply add the dependency (report §5.1 says so itself).
- **Design.**
  - A `FakeContext` (with `cancelled`, `deadline`, `remaining`, `exec`, and recorded artifacts and events) is valuable.
  - `run_local` risks becoming the shell entry point that T4 and Decision 3 reject. Ship it as a pytest fixture only.
  - Note that `forbidden_import_error` (`trestle/child/validate.py:58-89`) only admits `trestle.plugin` exports, so `trestle.plugin.testing` stays unimportable from entry files. That is good.
- **Size.** M → S.
- **Verdict: reshape.** Ship `FakeContext` in `trestle.plugin.testing` (or promote `packages/trestle-packs/tests/fake_pack_context.py`). No new distribution and no harness.

### R4 plain-plugin `Blocked`, `requires_tools`, `env=` (S/M)

- **Evidence.** Holds. `zip:compose_up/aws_preflight.py:108-112` always takes the no-TTY branch under Trestle, because stdin is `/dev/null` (`plugins.md:151`).
- **Already decided?** Only for credentials: step 3/4 give `execution.credential_interactive` (`:263`, `:459`). A general plugin-raised `Blocked` is new.
- **Design.**
  - The tree's `Blocked(code, human_action, resend)` (`trestle/workflow/units.py:61-65`) is the right shape. Plugin-minted codes need a namespace, for example `plugin.<id>.<code>`, or a restriction to the vocabulary, so a plugin cannot forge `admission.*` or `execution.*`.
  - `env=` collides in name with R1's `ctx.exec(env=)` and with `env_arg`. Call it `requires_env`.
  - `requires_env` is meaningless until `config.toml` can supply values (G1).
  - `requires_tools` resolving "on the service PATH" conflicts with the env pack's rule of "never a tool found on `PATH`" (`docs/environment.md:111-115`). At minimum, record the resolved absolute path in provenance.
  - Identity cost is avoidable. Follow the `repeatable` precedent of "only when declared" (`trestle/common/types.py:199-200`), and no existing snapshot id or golden changes.
  - The acceptance test names a real profile (`dev-generic`). Per the AWS-fakes rule, prove it against a stub `aws`.
- **Size.** S for `Blocked`; S for the requires keywords after G1.
- **Verdict: keep `Blocked`; reshape the requires half** (rename it, make it depend on G1, record resolved paths).

### R5 multi-key leases / T7 (M)

- **Evidence.** Holds. Write races are real (`zip:github_pr/open.py:85-99`, `batch.py:254-258`).
- **Already done?** The `held_by`/`free_at` part is already change 2b (`:455`). Don't count it as new.
- **Design.** "admission already handles `lease_set`" is half true. Every consumer reads `lease_set[0]`:
  - `pools.busy_until(state, plan.lease_set[0])` (`admission.py:403`);
  - `created` row `lease_key` (`admission.py:735-737`);
  - `trestle/server/lease.py:1-10`: one key, `sched.json`, "v0.3.0 runs included".

  Multi-key needs these changes:
  - a new record field;
  - multi-key FIFO in `sched.json`;
  - mixed-version homes, where an older server sharing the home sees only the first key. That breaks mutual exclusion, so a home-format bump is needed.

  There is also a key-namespace problem. Keys are opaque canonical JSON shared across plugins *on purpose* (`lease.py:3-4`), so literal `"port:9000"` keys need a reserved prefix scheme, and `git_root` paths need `realpath` canonicalization.
- **Simpler interim.** Pack-side `env_arg="repo"` for single-repo suites.
- **Size.** M → L.
- **Verdict: defer behind 2b.** Do it as its own L change with a home-format note, not in an early bundle.

### R6 publication lints (S each)

- **(a) Undeclared non-stdlib import.** Holds: `examples/packs/migrate_apply.py` is a bare `@trestle` importing `trestle_packs`. A warning is cheap. "Refusal after one release" breaks existing operator plugins, so keep it a warning. Also fix the four examples (add `packages=["trestle_packs"]`). **Keep, S.**
- **(b) `subprocess` or `time.sleep` in the entry module.** It catches nothing in the case that motivated it: all 30 calls are in the package, and the wrappers are thin. **Drop.**
- **(c) Undeclared write effects.** Core already does a version of this. `imported_port_modules` forces `env_arg` when a plugin imports port modules (`trestle/child/validate.py:98-130`). But the list is hard-coded as `trestle_packs.<name>` (`:102-114`), which is exactly the name the fork reuses with *different* modules. **Reshape** as G4.

### R7 durable lifetime for plain plugins (L)

- **Evidence misread.** The module R7 proposes to promote says the opposite: "A `DURABLE` creation is refused before any change: a process this port starts is the run's, never a host-kept one" (`packages/trestle-packs/trestle_packs/process/local.py:9-11`, B3-C3/C4). The conductor kills on *every* terminal path, success included (`conductor.py:266-273`).
- **Design.** A host-kept process domain under a per-user durable service is a new ownership authority. It needs:
  - its own decision record;
  - a sweep story;
  - restart-across-upgrade semantics, because `service upgrade` drains and restarts.
- **Verdict: defer**, and needs a decision. Ship only the doc line now ("detached services die with the run, except double-forked daemons").

### R7b thread-safe Context (S)

- **Evidence.** Holds, and it is worse than stated. Beyond the unguarded counters, `append_ndjson` repairs the file tail *before* appending (`trestle/common/fsutil.py:52-66`). Two threads interleaving repair and write can corrupt K-18 framing in `events.ndjson`, which the branch's live `run_events` and progress relay now read mid-run.
- **Verdict: keep, S, rank 1.** One lock around `_emit`, `attach`, `_record_limit` and `flush_limits`.

### R8 `status`, `attach_text`, `table` (S)

- **Already done?** On the branch:
  - same-name replace takes no count slot, and docs say a plugin "can rewrite `status.md` every round of an hour-long run" (branch `docs/plugins.md` T3 paragraph);
  - `summary_fields` `name[cols]` tables cover T6 (branch `plugin_schema.py:497-513`).
- **The cost argument.** "240 versions" is about 1 MB against a 2 GB byte cap (`limits.py:18`). Dropping history would also reverse T3's deliberate "old ids stay fetchable".
- **Verdict: drop.** `attach_text(max_bytes)` and list-of-dicts rows are pack-side fixes.

### R9 provenance and versioning (M)

- **Design.**
  - Recording the distribution name and version is cheap and resolves the fork ambiguity for humans. Keep it *display-only*, outside snapshot identity, so existing snapshots and goldens keep their ids.
  - Digesting non-`.py` data changes identity for every `packages=` snapshot: defer it.
  - `requires_surface` duplicates a pip version constraint on `trestle`: drop it.
  - The report missed the more important provenance hole, G2.
- **Size.** M → S.
- **Verdict: reshape.**

### R10 public conformance kit (M)

- **Evidence conflated.** `packages/trestle-packs/tests/conformance/*_cases.py` are *port* conformance suites (container, grant, provision), not plugin-behaviour suites (cancel within grace, summary fits). The proposed kit is new work.
- **Verdict: defer** until R1, R3 and R4 exist to test against.

### R11 tree on-ramp (M)

- Plausible, and the friction is real (`docs/plugins.md:251-474`). It does not unblock anything urgent, and the new module must be added to `PUBLIC_MODULES`, or plugins cannot import it (`validate.py:52-56`).
- **Verdict: defer.**

### T5 entry points, narrowed (M)

- **Design.**
  - Loading entry points with `ep.load()` imports third-party code into the durable service. That breaks the static, import-free publication posture (`validate.py:203-205`, "found without importing anything").
  - Resolving `ep.value` to a file without importing avoids that, but the report does not say so.
  - **Simpler alternative that already works:** the pack ships its wrappers as a directory, and the operator adds it to `[plugins] paths`. The `service-mode` branch's `docs/packs.md:13` already documents exactly this flow for `examples/packs`.
  - Auto-publishing on every service start also silently accepts upgraded pack code that R-J says needs a deliberate republish (`plugins.md:72-75`).
- **Size.** M → S.
- **Verdict: reshape to S.** Ship the wrappers in-package, document `paths`, and optionally add a `[plugins] packs = ["trestle_packs"]` sugar that resolves the directory with `find_spec`-free location. Defer true entry-point loading.

### T7 exclusivity

Same as R5: accept the direction, defer as L behind 2b.

## Gaps the report missed

1. **G1: no plugin environment under service mode.** The unit's environment is exactly PATH, HOME and TRESTLE_HOME (`service-mode:trestle/server/service.py:6,73-75`). The record says the rest comes "in `config.toml`" (`:142-144`), but `trestle/server/config.py` has no env table. That breaks the shipped env pack's operator variables (`TRESTLE_DOCKER_PATH`, `TRESTLE_IMAGE_*`, `TRESTLE_ENV_COMPOSE_FILE`; `docs/environment.md:17-25`) as well as the zip's `DEFAULT_AWS_PROFILE`. Unattended runs will fail obscurely the day service mode lands. Fix it with an operator-only `[plugins.env]` or `[env.<plugin>]` table, never from args. **S/M, high.**
2. **G2: grants pin only the entry file.** `source_sha256` is the digest of the entry file alone (`trestle/server/snapshots.py:158`); package digests are a separate identity slot (`:159-163`). Decision 2 binds grants to `source_sha256` (`:240-246`). Combined with packs (T5, R9), a granted thin wrapper keeps its grant while the library under it changes: a republish after `provenance_mismatch` leaves `source_sha256` unchanged. Amend the record to pin `(source_sha256, package_digests)` or the `snapshot_id`. **Doc S, before step 3.**
3. **G3: no cooperative stop for plain plugins.** Release slice 0 and no SIGTERM handler mean a cancel or deadline kills mid-write, and the return value is lost. Options: a declared `@trestle(stop_grace=...)` slice, or a child SIGTERM handler that raises in the main thread within `grace`. This is what R2 was reaching for. **M, needs a decision.**
4. **G4: port-module detection is hard-coded to one distribution's names** (`validate.py:102-114`). The fork's `docker`, `app_up` and `system_up` escape the `env_arg` rule. A fork shadowing `trestle_packs.process` changes what the rule means. Let a pack declare its port modules (package metadata read without import), or key the rule on the distribution plus a version. **S.**
5. **G5: pack lifecycle under the service.** Packs must live in the service binary's interpreter. `trestle service upgrade` pip-upgrades only `trestle`, and the pack's dependency on `trestle` is unpinned, so an upgrade can strand or break packs with no signal. `status` and `describe_plugin` should show the pack versions (R9 display), and `upgrade` should report or refuse incompatible installed packs. **S.**

## Corrected ranked list

| # | Item | Verdict | Size (theirs → mine) | Reason |
|---|---|---|---|---|
| 1 | R7b thread-safe Context | keep | S → S | Concurrent `ctx.log` can corrupt live-read `events.ndjson` framing, not just counts |
| 2 | R1 `ctx.exec` | keep, reshaped | M → M | Real; capture-by-default, inherited env, `credentials=` reserved, 2c on top |
| 3 | G1 plugin env in `config.toml` | new | — → S/M | Service mode otherwise breaks existing env-pack plugins |
| 4 | R4a plain `Blocked` | keep | S/M → S | Unattended runs need `human_action`, not failure text; namespace codes |
| 5 | G2 grant pin covers packages | new | — → S (doc) | Closes a credential hole before step 3 |
| 6 | R6a undeclared-package warning + fix examples | keep | S → S | Cheap; examples have no `packages=` |
| 7 | R3 `FakeContext` only | reshape | M → S | Packs already can depend on `trestle`; no new distribution |
| 8 | R4b `requires_tools`/`requires_env` | reshape | (in R4) → S | Depends on G1; record resolved paths |
| 9 | R9 dist version (display-only) + G5 | reshape | M → S | Fork disambiguation without identity churn |
| 10 | T5 in-package wrappers + `paths` | reshape | M → S | Already works; entry-point import breaks static loading |
| 11 | G4 declared port modules | new | — → S | Hard-coded names fail on forks |
| 12 | G3 plain-plugin stop window | new | — → M | Replaces R2; needs a decision |
| 13 | R5/T7 multi-key leases | defer | M → L | Single-key record, `sched.json`, mixed-version homes |
| — | R11 tree on-ramp | defer | M → M | Useful, not urgent |
| — | R10 conformance kit | defer | M → M | Report conflated port suites with plugin suites |
| — | R7 durable lifetime | defer (decision) | L → L | Contradicts B3-C3/C4 refusal of `DURABLE` |
| — | R2 waits / margin | drop (fold `remaining()` into R1) | S → XS | Cancel is already forced; margin semantics wrong |
| — | R6b, R6c | drop / replace by G4 | S → — | (b) catches nothing; (c) exists as `PORT_MODULES` |
| — | R8 `status`/`attach_text`/`table` | drop | S → — | Covered by T3 replace and T6 tables on the branch |

## First bundled PR

**R7b + R1 (with `remaining()`) + R3's `FakeContext`.** They form one coherent unit: the plugin Context becomes thread-safe, gains the subprocess primitive, and gets a fake that implements it, so pack authors can test `exec` without a server. It touches `trestle/child/context.py`, `trestle/plugin/surface.py`, a new `trestle/plugin/testing.py`, and `docs/plugins.md`.

It depends on three things:

1. **`live-run-visibility` lands first.** R7b's lock must cover `_attached` and the T3 replace, which exist only there. `ctx.exec`'s artifact path relies on same-name replace to stay under the count cap.
2. **A one-paragraph amendment to `service-and-credentials.md`.** It moves `ctx.exec` (without `credentials=`) ahead of 2c, and makes 2c a wrapper over it, in the "As implemented" style `service-mode` introduced.
3. **An explicit choice to inherit the child's environment.** That keeps it independent of G1. G1 plus R4 (`Blocked`, `requires_*`) is the natural second PR, landing with or right after `service-mode`.
