# Trestle packs analysis: what core should change

2026-10-19. Read-only. Inputs: `~/Downloads/trestle-packs.zip`, extracted to `scratchpad/packs-zip/trestle-packs/` (cited below as `zip:`). Core `master`, `live-run-visibility` (worktree) and `service-mode`. Nothing in the zip was run or imported, and git was never run inside it.

## Headline

The zip is a **diverged fork** of trestle-packs (v0.1.0, with no dependency on `trestle`). It does not contain a single `@trestle` plugin. It is a library of plain functions plus two **shell CLIs**, and the wrappers live in consumer repos. Its packs are aimed at the plugin surface, but they re-implement, by hand and less safely, what core already has or has planned:

- subprocess control: 30 raw `subprocess` calls, about 5 with a timeout;
- cancellable waits: 11 `time.sleep` loops;
- deadline arithmetic: a hard-coded copy of the 35 s finalization margin;
- BLOCKED verdicts and gates: `chain.GateFailed`;
- process ownership: by port occupancy and bare PID, which core's own process port forbids;
- rolling status artifacts.

The best changes put a small number of these primitives on plain-plugin `Context`, so pack authors stop rebuilding them:

- `ctx.exec`
- `ctx.wait_for`
- `ctx.remaining`
- `Blocked` for plain plugins
- a published `trestle-surface` protocol with a fake context

Then catch the risky patterns at publication: undeclared imports, raw subprocess calls, undeclared write effects. Then fix provenance, so pack code is pinned and versioned instead of drifting in two forks.

**T5 (entry points), narrowed:** the operator opts packs in by name. **T7 (exclusivity), adopted:** as multi-key leases built on the existing `env_arg` lease machinery.

## 1. Inventory

### Packs in the zip (`zip:trestle_packs/`)

| Pack | Purpose | Entry functions | Plugin-surface use |
|---|---|---|---|
| `github_pr` | Open, monitor, ci_report and comments over N PRs (gh + git) | `run_github_pr_suite` (`batch.py:78`), CLI `github-pr-suite` (`cli.py:58`) | `ctx.log`, `ctx.progress(fraction)` once per poll round (`batch.py:393`), `keep()`, which re-attaches `suite.json`, `suite.md` (`suite.py:64-68`) and `monitor-status.md` (`batch.py:396`). Reads `ctx.deadline` and `ctx.cancelled` with `getattr` (`batch.py:320`, `chain/__init__.py:55-61`) |
| `chain` | Cases as data, per-group setup, gates leading to BLOCKED, suite report | `Chain.run_suite` (`chain/__init__.py:201`) | progress per step (`:175`), log on failure (`:187`), trail, `suite.md` and `suite.json` artifacts (`:196-238`) |
| `system_up` | Manifest-driven hybrid stack: vendor containers plus host-run locals | `up_stack`, `restart_local`, `stop_local` (`orchestrator.py:93,190,172`), CLI `local-system-up` | **No ctx at all.** Uses `logging` (`orchestrator.py:290`), JSON to stdout (`cli.py:152-176`) |
| `compose_up` | AWS STS preflight, vendor bootstrap scripts, compose waves | `prepare_compose` and `up_compose_wave` (orchestrator), `run_vendor_bootstrap` (`bootstrap.py:44`) | none. Runs `bash` scripts (`bootstrap.py:178`) and `aws sso login` (`aws_preflight.py:102-123`) |
| `app_up` | Heal, reclaim, start and probe for local Python and Gradle apps | `ensure_and_start`, `stop_project` (`launcher.py:33,279`) | none. Detached `Popen(start_new_session=True)` (`launcher.py:243-250`) |
| `kafka`, `logs`, `http_stub` | System-test building blocks | `KafkaCli`, `wait_for_patterns`, `HttpStub` | `logs` takes a `live` callback (`logs/__init__.py:50`) |
| `core/artifacts` | Its own `PackContext` protocol plus `keep()` | none | redeclares the Context protocol **without** `cancelled` and `deadline` (`core/artifacts.py:10-14`) |

The zip uses none of these: `summary_fields`, `deadline`, `packages`, `secrets`, `env_arg`, `repeatable`, `ctx.event`, `ctx.outputs`, the workflow tree, effect facets or container ports. That is because no wrapper ships with it. The wiring is two console scripts (`zip:pyproject.toml:24-26`) and the README's `python -m pip install -e` (`zip:README.md:11-14`). The real user is an external `systest/billing_systest.py`. Its README calls `ctx.artifact()` "only stages; nothing reaches evidence until attached" (`zip:README.md:83`, `core/artifacts.py:42`). That is wrong: core promotes staged files when the run ends (`docs/plugins.md:91-93`). The difference matters only for live visibility.

### Diff against `packages/trestle-packs` on master (v0.3.1)

- **Only in the repo:** `container`, `fakes`, `grant`, `process` (pid+start-time identity, `process/identity.py`), `provision`, `testrun`, `toolchain`, the conformance kit (`tests/conformance/*`), and `py.typed`.
- **Only in the zip:** `compose_up`, `app_up`, `system_up`, `chain`, `kafka`, `logs`, `http_stub`, `github_pr`, and `keep()`.
- **Shared but different:**
  - The zip **lacks** fixes the repo has: the duplicate-service check in `core/dag.py`; the `CancelToken` and `PollCancelled` in `core/readiness.py` (BFD-45); `reset_volumes` (K-6) in `docker/runner.py`; error counting in `pytest/runner.py`.
  - The zip **adds** what the repo lacks: multiple `compose_files`, `compose_profiles`, `no_deps`, and `stop(services=)` in `docker/spec.py`, `docker/compose_whale.py` and `docker/backend.py`.
- The fork split from the 2026-09-07 baseline (`zip:.git/logs/HEAD`, first commit). Neither side can take the other's fixes without a manual merge.

## 2. Friction and risk, with evidence

**F1. Raw subprocess calls with no bound.** 30 `subprocess.run` or `Popen` calls across 16 files; about 5 pass `timeout=`. Examples:

- `git push` with no timeout (`github_pr/open.py:98`, `_git` at `:202-212`);
- `bash dockerLogin.sh` and the other bootstrap scripts (`bootstrap.py:178`);
- `docker compose pull` and `up` with inherited stdout (`bootstrap.py:243,265`);
- `poetry`, `uv` and `pip install` (`heal_python.py:107`).

Core's enforced deadline kills these eventually. But a step that hangs takes the whole budget, its output is unbounded, and the reason is lost.

**F2. Waits that ignore cancel.** 11 `time.sleep` loops, for example `batch.py:362` (sleeps up to 15 s), `logs/__init__.py:60`, `ports.py:53` and `owner.py:90`. Plain plugins have no cancellable wait. `ctx.cancellation.wait_for` exists only for tree units (`docs/plugins.md:422-430`). The repo fork added a `CancelToken` to `poll_until`; the zip fork does not have it.

**F3. Deadline arithmetic re-derived.** `FINALIZATION_MARGIN_S = 35.0` (`batch.py:50`) copies `clock.finalization_margin`, and `_effective_budget_s` (`batch.py:318-324`) re-derives "time left". If core retunes `TRESTLE_CANCEL_GRACE_S`, the pack silently goes wrong.

**F4. A hand-rolled Context protocol and fake contexts.** Two copies exist: `core/artifacts.py:10-14` and `tests/fake_pack_context.py`. A third, divergent one is `_CliContext` (`github_pr/cli.py:19-55`), which streams to stderr. They are needed because packs must not depend on `trestle` (`zip:pyproject.toml:11-13`) yet must run standalone. That is why `cancelled` and `deadline` are read with `getattr` (`chain/__init__.py:57-60`).

**F5. Shell entry points.** The `local-system-up` and `github-pr-suite` console scripts exist because "the run never was a Trestle run" (proposal, Why). That conflicts directly with the no-shell rule and Decision 3 (MCP only).

**F6. Unsafe process ownership.**

- `reclaim_port` sends SIGTERM and then SIGKILL to **any** listener on 9000 or 9010 (`ports.py:36-63`). It is on by default from `launcher.py:70-75`.
- `reclaim_stale_pid` kills a recorded bare PID with no start-time check (`state.py:103-115`), so a reused PID is a risk.
- `_compose_container` picks the first container with a matching service label in **any** compose project (`boot_heal.py:43-51`).

Core's own `LocalProcessPort` forbids identity by port occupancy (WR-OWN-7) and kills only a recorded (pid, start) (`master:packages/trestle-packs/trestle_packs/process/local.py:12-16`). That helper is pack-private, not a public API.

**F7. Lifetime mismatch.** `app_up` detaches services with `start_new_session=True` and expects them to outlive the call (`restart_local` between cases; `orchestrator.py:190-204`). Core stops every attributable descendant "in whatever session it has moved to" when a run ends (`docs/plugins.md:131-137`). Started from one plugin run, those services die when the run ends, except the Gradle daemon's double-forked JVM, which escapes attribution (`launcher.py:286-291`). There is no plain-plugin way to declare "this outlives the run" (the tree has `Lifetime.DURABLE`, `workflow/declarations.py:61-63`).

**F8. Undeclared write effects and races.**

- `github_pr` stages, commits (with a fixed message) and pushes (`open.py:85-99`), then creates or **edits** PRs (`open.py:132-138`), from a thread pool (`batch.py:254-258`).
- `compose_up` runs `up --remove-orphans` on a shared project (`bootstrap.py:259`) and `system_up` stops containers (`orchestrator.py:225`).

None of this is declared. Two runs on the same git root or compose project race. `env_arg` gives exactly one key (`docs/plugins.md:44-51`), and the zip uses none.

**F9. Thread safety.** `open_one` calls `ctx.log` and `keep` from worker threads (`batch.py:213,223,249,251`). `RuntimeContext` has no lock: `_event_count`, `_window_count`, `_artifact_count` and `_attached` are unguarded (`trestle/child/context.py:92-100,192-195,269-271`), so limit accounting and same-name replace can race.

**F10. Unbounded evidence.** `wait_for_patterns` re-reads the whole log on each poll and returns it as `LogMatch.text` (`logs/__init__.py:57,64-70`). The README pattern then attaches it (`run.keep("service.log", m.text)`). Rolling re-attach of `suite.md`, `suite.json` and `monitor-status.md` on every row and every round keeps every superseded version, and all of them count toward bytes (`context.py:167-169`).

**F11. Results that summarize poorly.**

- `SuiteDigest.to_dict` has no top-level verdict or counts, only `survey` and `targets` (`github_pr/models.py:125-146`).
- `SuiteResult.rows` are markdown strings (`chain/__init__.py:235-239`), which the T6 table form cannot render.
- `system_up` returns an `OrchestrationResult` whose `aws_preflight` may be an arbitrary object (`orchestrator.py:64-68`).

**F12. A pack cannot tell the agent what it needs.**

- The AWS SSO path returns "run: aws sso login" text when there is no TTY (`aws_preflight.py:108-112`). A plain plugin cannot end `blocked` with a `human_action`; only tree units can (`workflow/units.py:61`).
- Required tools (`gh`, `git`, `docker`, `aws`, `lsof`, `bash`) and env (`DEFAULT_AWS_PROFILE`, `aws_preflight.py:70`, read via `os.environ.copy()` at `launcher.py:105` and `layout.py:95`) are implicit.
- Under service mode the unit's environment is exactly `PATH`, `HOME` and `TRESTLE_HOME` (`decisions/service-and-credentials.md:120`). These packs will fail obscurely there.

**F13. Provenance gap.**

- The in-repo pack wrappers use bare `@trestle` with no `packages=` (`master:examples/packs/*.py`), so pack code is neither recorded nor checked (`docs/plugins.md:59-71`).
- `packages=` digests `.py` only, and records no distribution version.
- Two forks share the name `trestle_packs`, so a digest cannot tell which one ran.

**F14. Reinvented tree semantics.** These core concepts are each rebuilt in the zip:

| Core concept | Zip re-implementation |
|---|---|
| Waves (`needs`, `concurrency`) | `core/dag.py`; `open_waves` (`batch.py:271-287`) |
| Gates and BLOCKED | `GateFailed` (`chain/__init__.py:39-44`) |
| Remedies (`LeafDeclaration.remedies`, `declarations.py:154`) | boot-heal remedies (`boot_heal.py:54-60`) |
| Readiness wait (`WaitPolicy`) | `probe_until_ready` (`probe.py:62-91`) |

Authors skip the tree API because the minimal example is about 130 lines for three markers (`docs/plugins.md:251-380`).

## 3. Recommendations (ranked by value to pack authors and agents)

**R1. `ctx.exec` as the general subprocess primitive (support, M).**

- *Problem:* F1, F2, F12; 30 unbounded calls.
- *Change:* pull Decision 2's `ctx.exec(argv, ...)` (`service-and-credentials.md:263`, sequence step 5) forward and generalize it, before credentials. `ctx.exec(argv, cwd=, env={...}, timeout=None, stdin=None, output="artifact:<name>"|"capture", tail_lines=50)`:
  - the timeout defaults to `ctx.remaining()`, and the process tree is killed on cancel;
  - stdout and stderr are bounded and go to an attached artifact;
  - it returns `{code, tail, artifact, duration_s}`;
  - the environment is the allowlist plus `env`, never a full `os.environ`;
  - `credentials=` is added later, unchanged.
- *Lands in:* `trestle/plugin/surface.py`, `trestle/child/context.py`, with a doc section in `plugins.md`.
- *Risk:* additive. It must stay an argv list with no shell, to keep the no-shell posture.
- *Acceptance:* a `ctx.exec(["sleep","999"])` in a run cancelled at t+2 s returns within `grace`; a 10 MB output becomes one artifact and a tail of 50 lines or fewer.

**R2. Cancellable waits and time left on plain plugins (support, S).**

- *Problem:* F2, F3.
- *Change:*
  - `ctx.wait_for(pred, bound)` and `ctx.sleep(seconds)` return early on cancel or deadline; they reuse `WaitOutcome`, and the tree's `ctx.cancellation` delegates to them.
  - `ctx.remaining(margin="finalization")` returns seconds, with the margin read from `trestle.common.clock`.
- *Lands in:* `child/context.py`, `surface.py`.
- *Acceptance:* `batch.py`'s sleep and margin constant can be deleted, and a cancel during a 15 s poll interval stops the run in under 1 s.

**R3. A published surface package with a fake and a local harness (upgrade, M).**

- *Problem:* F4, F5.
- *Change:* ship `trestle.plugin.surface`, plus `trestle.plugin.testing.FakeContext`, as a dependency-free distribution (`trestle-surface`) or as a stable subpackage packs may import. The fake has `cancelled`, `deadline`, `remaining`, `exec` and a recorded artifact set. Give it a `run_local(plugin, args)` harness for **tests only**; it is not an agent entry point.
- *Lands in:* `trestle/plugin/`, packaging.
- *Risk:* the protocol becomes a versioned contract (see R9).
- *Acceptance:* the zip's `PackContext`, `FakePackContext` and `_CliContext` are replaced by imports, and the `getattr` reads go away.

**R4. Plain plugins can end `blocked` and declare needs (support, S/M).**

- *Problem:* F12.
- *Change:*
  - Add `raise trestle.plugin.Blocked(code, human_action, resend)` for plain plugins, reusing the tree's `Blocked` projection to `answer.primary`.
  - Add `@trestle(requires_tools=["gh","git","docker"], env=["DEFAULT_AWS_PROFILE"])`. Admission resolves tools on the service `PATH` and refuses with `admission.tool_missing` or `admission.env_missing` before a run id exists; the values come from `config.toml`, never from the agent.
- *Lands in:* `plugin_schema.py` (`DECLARED_KEYWORDS`, `:344`), `admission.py`, `projection.py`.
- *Risk:* the new keywords change snapshot identity.
- *Acceptance:* AWS preflight with no SSO ends `blocked` with "aws sso login --profile dev-generic", and a missing `gh` is refused before a run id.

**R5. Multi-key leases (T7 reshaped) (enforce, M).**

- *Problem:* F8, the proposal's T7, and the PR-edit race.
- *Change:* `@trestle(locks=["git_root", "manifest.targets[].repo", "port:9000"])`. Each entry is an argument path, or a literal key with a static prefix. They compile into the plain plugin's `lease_set`, which `admission.py:394-410,527-528` already handles for one `env_arg`. `env_arg` becomes sugar for `locks=[env_arg]`. The `environment_busy` refusal carries `held_by` and `free_at` (gap table, `service-and-credentials.md:395`).
- *Lands in:* `plugin_schema.py`, `admission.py`, `lease.py`.
- *Risk:* the ordering of multiple keys must be deadlock-free; acquire them sorted and all at once.
- *Acceptance:* two `github_pr_suite` runs naming the same repo give one run and one `environment_busy` with `held_by`.

**R6. Publication lints (enforce, S each).** At publication core already parses the AST (`plugin_schema.py:367-375`). Add three checks:

- (a) A non-stdlib, non-`trestle` import in the entry file that is missing from `packages=`: a warning in `describe_plugin` now, a refusal after one release. This also fixes `examples/packs/*.py`.
- (b) A direct `subprocess` or `os.system` call, or a `time.sleep` in the entry module: `lint` markers shown by `describe_plugin` (`lints: [...]`).
- (c) A wrapper that calls into a package exporting known write entry points without declaring `locks` or `effects`: an advisory.

Warnings only; nothing runs. *Acceptance:* publishing the `docker_stack` example yields `lint: undeclared_package trestle_packs`.

**R7. Durable-service lifetime for plain plugins (support, L).**

- *Problem:* F7. Stack-up plugins cannot keep services up between runs, and they kill by port.
- *Change:* expose a public `trestle.workflow.process` (pid+start identity, `LocalProcessPort` with `Lifetime.DURABLE`, host-kept and recorded in the home). Add a matching `query(view="services")` and a release plugin. Until then, document that detached services die with the run.
- *Lands in:* `trestle/workflow/ports.py`, the home store, `process/local.py` promoted from the packs.
- *Risk:* a new ownership domain outside a run. Needs a decision record.
- *Acceptance:* `app_up` can start an app in run A and stop it in run B by selector, with no port-occupancy kill.

**R7b. Thread-safe Context (enforce, S).**

- *Problem:* F9.
- *Change:* one `threading.Lock` around `_emit`, `attach` and `_record_limit`.
- *Acceptance:* 8 threads × 1000 `ctx.log` give exact counts, and concurrent same-name attaches leave one current row.

**R8. Status documents and bounded attach helpers (support, S).**

- *Problem:* F10, F11.
- *Change:*
  - `ctx.status(name, text)` writes a live document that **keeps only the latest version and its byte cost**, unlike the replace-with-history in `context.py:167-169`.
  - `ctx.attach_text(name, content, max_bytes=)` keeps the tail.
  - `ctx.table(name, rows, columns)` attaches `.md` and `.json` (the proposal's optional T6).
- *Acceptance:* a 1 h monitor at 15 s rounds uses bytes for one `monitor-status.md`, not 240.

**R9. Pack provenance and versioning (upgrade, M).**

- *Problem:* F13 and the fork drift.
- *Change:*
  - `packages=` also records the providing distribution's name and version (from `importlib.metadata`) and the digest of non-`.py` package data.
  - Add `requires_surface=">=0.4"` checked at publication.
  - `describe_plugin` and `run_provenance` show `trestle_packs 0.3.1 (sha…)`.
  - A distribution name collision (two different trees both called `trestle_packs`) becomes visible as different versions and digests.
- *Acceptance:* the zip fork (0.1.0) and the repo (0.3.1) are distinguishable in `run_provenance`.

**R10. Pack conformance kit as a public test harness (upgrade, M).**

- *Problem:* the repo already has `tests/conformance/*_cases.py` and `fakes/`, but they are internal; the zip has no equivalent and lacks BFD-45 and K-6.
- *Change:* publish `trestle.testing.conformance`, with suites for cancel-within-grace, deadline-respect, bounded output, no-undeclared-effects (checked against the fakes), and summary-fits-budget. Packs run it in their own CI.
- *Acceptance:* the zip's `github_pr` passes cancel and summary checks after R1–R3.

**R11. A smaller tree on-ramp (support, M).**

- *Problem:* F14.
- *Change:* `trestle.workflow.simple`. `step(unit, fn, needs=, budget=)` builds a `LeafDeclaration` with `NoAction` or `Acted` from a function, so the waves in `chain`, `system_up` and `github_pr` map onto `AllDeclaration` with per-part verdicts.
- *Acceptance:* the docs example shrinks under 40 lines, and `open_waves` becomes `needs`.

### Conflicts with recorded decisions

- **T4 / shell CLIs:** stay rejected. R3's `run_local` is test-only and offers no run id or ledger. The pack's `[project.scripts]` should go (see §5).
- The **`exec` plugin** (gap table) and **R1 `ctx.exec`** complement each other. R1 is in-plugin only; it must not become an agent-facing tool, and the surface stays at ten tools.
- **R4 `env=`** values come from `config.toml`, never from args or the inherited shell, which is consistent with Decision 1's environment.
- **R7** touches D-9 only through process lifetime, not credentials. Note the memory rule "AWS demo mode only": `compose_up` drives real `aws sso login`. Core must not grow real AWS wiring for it; R4's `Blocked` is the safe shape.

## 4. T5 and T7 verdicts

- **T5, discover plugins via entry points: accept, narrowed.** The evidence supports the friction: the zip ships no wrappers, the repo wrappers are examples, and the real consumers hand-copy wrappers. Blanket entry-point loading behind `entry_points = true` would publish whatever any installed distribution advertises into the service, and with the grants of Decision 2 that is too loose. Reshape it:
  - `[plugins] packs = ["trestle-packs"]` lists distributions **by name**;
  - only their `trestle.plugins` entry points load;
  - each gets `packages=` provenance automatically, with R9's version;
  - credential grants still pin each plugin's own `source_sha256`.

  Size M. It depends on R9.
- **T7, exclusivity: accept, as R5.** The packs show write races across several resources at once: a git root and repo (`open.py`), ports (`ports.py`), and a compose project (`bootstrap.py`). One `env_arg` cannot express these. Reuse the lease machinery rather than a new `exclusive=` key or a new code: refuse with `environment_busy`, extended with `held_by` and `free_at`.

## 5. Pack-side fixes that need no core change

1. Merge the forks: take the repo's BFD-45 cancel token, K-6, the DAG duplicate check and pytest error counts; push `compose_files`, profiles, `no_deps` and `keep()` up. Depend on `trestle`, and bump past 0.3.1.
2. Ship wrappers with `packages=["trestle_packs"]`, `deadline` (≤ `[operator] deadline_ceiling_s`; raise it in config for 60–90 min CI), `summary_fields=["ok","counts","targets[id,pr_number,verdict,check_verdict]"]`, and `env_arg` for the stack plugins.
3. Add top-level `ok`, `verdict` and `counts` to `SuiteDigest`. Make `SuiteResult.rows` a list of dicts.
4. Remove `[project.scripts]`, or keep them as a human-only tool documented as such.
5. Delete the default port reclaim, or gate it on an ownership check (`owner.process_belongs_to`) before signalling. Check the PID start time in `reclaim_stale_pid`. Filter `_compose_container` by compose project.
6. Add `timeout=` to every `_git`, script and compose call. Cap `LogMatch.text` to a tail.
7. Do `ctx.log` and `keep` only on the main thread in `_run_open_phase`; the report phase already does.
8. Fix the README's claim about `ctx.artifact` staging.
9. Make `PythonOpenBackend` use a commit message from the manifest, not the fixed `chore: ship via github_pr_suite`.

## 6. Unread or suspicious content in the zip

- `zip:.git/config` has `[include] path = /Library/Application Support/Bold Agent/Resources/bold_git_tools/config`. This is an external, unreviewed git config include that would apply to any git command run in that directory, so I ran none. I did not read the included file; it is outside the zip.
- I did not read or verify: about 120 `.pyc` files (cpython-313 and 314; they cannot be confirmed to match the sources), `.git/objects` and `.git/index`, and `.pytest_cache` and `.ruff_cache`. `.git/hooks` holds only `*.sample` files.
- I found no network endpoints beyond GitHub URL parsing and localhost health probes. The packs do run host-mutating tools when invoked: `git push`, `gh pr create`/`edit`, `aws sso login`, vendor `bash` scripts (`dockerHosts.sh` probably edits hosts), `pip`/`poetry install`, and SIGKILL by port.
- The data has personal and organizational details: author `<author email>`, internal repo names, and absolute paths under `<user-home>/` (an example manifest).
- I found no embedded instructions aimed at an agent.
