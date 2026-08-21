# User Stories — Trestle (from requirements)

## Guiding Light

Upstream calls this document must serve. Plain language; no requirement IDs.

**Upstream:** [trestle-requirements.md](trestle-requirements.md) (Draft v0.6)

### Why this project exists

- Agents write and run CLI tools and scripts; those tools must stay usable instead of flooding every turn with output.
- Powerful work still needs a record: keep the evidence and let the agent retrieve telemetry, rather than dumping everything into context.

### What must be true upstream

- Keep default replies to a status frame plus the minimum needed to name the next retrieval; refuse without a run id when admission fails.
- Contain hostile plugin output without flooding agent context or breaking the control plane.
- Extend capability by writing plugin files — the core MCP tool count must not grow with plugins.
- Let agents wait, query, and fetch without polling loops or backend-specific grammar.
- Reach terminal-sensitive work optionally — same run class when offered, named exclusion when not.

### This document's job

- Decompose requirements into JTBD stories with pass/fail acceptance criteria traceable to the source spec.

## Source

`trestle-requirements.md` (Draft v0.7, 2026-08-20). No other local documents were used. Requirement IDs in acceptance criteria are citations, not extra scope.

Trestle is a durable local execution ledger with an MCP control surface: the ledger is truth, the run directory is evidence, workers are disposable, query backends are caches, and the agent sees only projections (framing; R-INV-1). v0.1 is localhost POSIX, single user; Windows, remote, multi-tenant, sandboxing, and workflow-engine composition are out of scope (§2.1–2.3).

## Stories

### US-01 — Finish local work without filling the context window

**JTBD:** When I have a local tool job to complete through Trestle, I want every default reply to carry only enough status and next-step identity to continue, so I can finish the job at lower context cost per success rather than ingesting full evidence on every turn.

**Semantic contract**
- Actor: coding agent calling Trestle over MCP (or equivalent CLI)
- Situation: a local job that will produce more evidence than a status frame
- Motivation: spend scarce context on progress, not on retained bytes
- Progress: the job can complete with default tool results limited to the status frame plus the minimum needed to name the next retrieval; full evidence remains on disk, not in the default payload

**Acceptance criteria**
- [ ] Default tool results contain only the status frame plus the minimum information needed to identify the next retrieval operation (G1, G4, §10.3).
- [ ] Status frame includes run identity, state, duration, counts; errors are compact and do not include tracebacks (R-BUD-13–17).
- [ ] Framework-observable evidence is retained up to declared capture and storage limits; anything suppressed is accounted for deterministically — never silently discarded to shrink the reply (G4 vs G1 conflict resolution; R-LIM-3).
- [ ] Untrusted plugin-controlled bytes do not enter the MCP process except through a bounded, deterministic projection (R-INV-1), including return values, stdout/stderr, events, artifact metadata, errors, plugin descriptions, query results, and fetch results.
- [ ] Core MCP tool count does not grow with plugin count (R-MCP-1). `list_plugins` does not return schemas (R-MCP-2).
- [ ] A feature that neither reduces agent context nor improves reliability of arbitrary tool execution is not treated as in-scope for v0.1 (acid test, §1).

**Non-goals**
- Does not cover waiting without polling (US-04).
- Does not cover pulling a specific slice of retained evidence (US-05).
- Does not cover TTY-class reach (US-16–US-19).
- Does not claim host health or other-run health under a hostile plugin (US-08).

**Why this framing works**
G1 and G4 are the product thesis: retain almost everything, surface almost none. Framing this as context cost per completed task (R-COST-1, R-COST-6) keeps the story from collapsing into “return smaller JSON.” Isolation from implementation (G8) is the same job: the agent must not need to know how projection is built.

**Remaining assumptions**
- λ (turn vs token tradeoff) is calibrated at M7 (R-COST-3); this story does not wait on that number to be true as a design constraint.

---

### US-02 — Treat a refused request as “nothing ran”

**JTBD:** When Trestle will not accept a call, I want a request outcome with no run identity, so I do not search history, wait, or fetch as if work had started.

**Semantic contract**
- Actor: coding agent
- Situation: admission cannot proceed (unknown plugin, bad args, full queue, draining, idempotency conflict, TTY-class missing/not ready, and similar named outcomes)
- Motivation: keep “did not start” distinct from “started and ended badly”
- Progress: I can retry, change args, or leave Trestle without a `run_id` that implies a ledger row

**Acceptance criteria**
- [ ] `rejected` is not a run state. Admission refusal is a request outcome with no `run_id` (R-RUN-1).
- [ ] Named request outcomes including `plugin_not_found`, `invalid_args`, `queue_full`, `idempotency_key_conflict`, `service_draining`, `tty_not_ready` have no `run_id` (R-MCP-4, R-EXEC-27, R-EXEC-32, R-TTY-6).
- [ ] Excess beyond bounded queue depth is `queue_full` as an admission outcome, not a queued run (R-EXEC-27).
- [ ] Internal run state is not constrained to match protocol vocabulary (R-RUN-7); MCP Task status, if present, is a projection of run state, not a second primitive (R-MCP-10).

**Non-goals**
- Does not cover post-admit failure classification (US-03).
- Does not cover TTY-class unreadiness vs validation failure distinguishability (US-18) beyond the shared “no `run_id`” rule.

**Why this framing works**
Request versus run is load-bearing vocabulary (§Normative vocabulary). If refusal looks like a failed run, every later story (wait, query, fetch, TTY incompleteness) becomes ambiguous.

**Remaining assumptions**
- Exact admission-code catalog beyond the examples in R-MCP-4 is design; this story only requires “no `run_id`” and distinguishability from terminals.

---

### US-03 — Know how a started run actually ended

**JTBD:** When a run I already hold has finished, I want a single immutable terminal class that matches what happened, so I can decide whether to trust the result, retry, or inspect retained evidence.

**Semantic contract**
- Actor: coding agent (and operator reading the same ledger)
- Situation: execution has ended and evidence is finalized
- Motivation: recover correctly from exception vs dirty exit vs signal vs cancel vs timeout vs crash recovery
- Progress: I can name exactly one terminal state; I never see a terminal before evidence finalization is durable

**Acceptance criteria**
- [ ] Terminal states are immutable (R-RUN-4). Exactly one terminal transition (R-RUN-14).
- [ ] Agent does not see terminal before `evidence_finalized` is durable (R-STORE-15). A terminal record follows `evidence_finalized` (R-RUN-16).
- [ ] Clean return → `succeeded`; uncaught exception → `failed`; `SystemExit`/nonzero clean exit → `worker_exit`; signal/OOM → `crashed`; these last two are distinct (R-EXEC-22, R-RUN-6).
- [ ] Cancel follows flag → grace → SIGTERM group → kill group (R-EXEC-16); timeout uses the same path and ends `timed_out` (R-EXEC-18). `interrupted` is terminal and is not automatically resumed in v0.1 (R-RUN-5).
- [ ] Finalization failure is not `succeeded`; it is `failed`/`finalization_failed` (R-STORE-16). Capture exhaustion does not by itself fail the run (R-LIM-4).
- [ ] Evidence written before termination is retained (R-EXEC-20). `result.json` is not synthesized for failures (R-STORE-12).

**Non-goals**
- Does not cover crash-restart recovery algorithm (US-10).
- Does not cover TTY-class “succeeded with holes” vs incomplete (US-17).
- Does not cover live observation of a still-running run (§20; R-TTY-9).

**Why this framing works**
G5 (file every consequence under the request) and the state machine (§3.1) are one job: after admission, the ledger must tell a truthful ending. Splitting this from refusal (US-02) is the design effect of R-RUN-1.

**Remaining assumptions**
- Compact error shape in the status frame is specified at R-BUD-13–17; field layout is design.

---

### US-04 — Wait out a long job without burning a turn per poll

**JTBD:** When a job will take minutes, I want to wait inside one invocation or one join, so I do not spend a model turn on every poll interval.

**Semantic contract**
- Actor: coding agent
- Situation: a run is in progress and the agent has nothing useful to do until it is terminal (or a join condition is met)
- Motivation: wait without polling (G6)
- Progress: I receive status frames until done or timeout; joining does not occupy a plugin worker

**Acceptance criteria**
- [ ] An agent waiting on a five-minute job does not burn a turn per poll interval (G6).
- [ ] `wait_ms` default 2000; otherwise the call may return `{run_id, state: running}` (R-WAIT-1–8 as specified).
- [ ] `await_runs` supports all / any / first_failure; timeout returns partial; replies are status frames only (R-WAIT-1–8).
- [ ] Joins do not occupy a worker (R-WAIT-1–8). `await_runs` is a Trestle abstraction, not an MCP Tasks primitive (R-WAIT-9, R-MCP-13).
- [ ] Notifications are never the correctness mechanism for wait (R-REG-2; §2.6).
- [ ] Sampling is not used (R-SCOPE-3). FastMCP’s Docket/Redis task extra is not used as Trestle’s executor (R-FMC-2, R-FMC-8). v0.1 does not implement a Trestle-owned Tasks mapper; wait correctness is `wait_ms` / `await_runs` on stdio (R-WAIT-4).

**Non-goals**
- Does not cover querying named views of history (US-06).
- Does not cover live tail of a still-running run (deferred; §20, R-TTY-9, R-QB-28).
- Does not implement nested calls or pipelines (§2.4, §20).

**Why this framing works**
G6 is independent of G4: you can return tiny status frames and still fail the product if the agent must poll. Keeping wait as Trestle’s join (not FastMCP’s executor) is a requirement constraint that will force design seams.

**Remaining assumptions**
- v0.1 assumes stdio hosts can hold a multi-minute `tools/call` (closed §25 Q1). A named client that cannot is a later amendment, not Docket.

---

### US-05 — Pull a bounded slice of retained evidence by handle

**JTBD:** When default output is not enough, I want to retrieve a named, bounded window of a result or artifact by opaque handle, so I can inspect what I need without loading the whole file or learning filesystem paths.

**Semantic contract**
- Actor: coding agent
- Situation: I already have a run or artifact handle from a prior call
- Motivation: retrieve evidence under G4 without violating R-INV-1 or G8
- Progress: I get a bounded range/head/tail/grep/jsonpath window, with truncation and scan budgets signaled, never a path-shaped fetch

**Acceptance criteria**
- [ ] Fetch supports bounded range / head / tail / grep / jsonpath; default last 50 lines; reports fraction; grep caps matches; binary returns metadata only (R-FET-1–7).
- [ ] `<run_id>/result` is a pseudo-artifact (R-FET-1–7). Server never loads `result.json` in full; it uses `result.index` (R-LIM-10, R-BUD-24).
- [ ] Fetch never accepts filesystem paths; path-shaped strings yield `invalid_handle` (R-FET-8).
- [ ] Expired, missing, abandoned, and not_found are distinguishable. Abandoned artifacts remain fetchable by default until retention expiry or collection (R-ART-3, R-FET-1–7).
- [ ] Scan is bounded: `max_scan_bytes` 64 MB and `max_scan_time_ms` 2000; overflow is partial + `scan_budget_exceeded` (R-FET-9).
- [ ] Handles on the MCP surface are never filesystem paths (vocabulary: handle). State travels as explicit handles, never hidden session state (R-SCOPE-4).
- [ ] UTF-8 text fetch: invalid sequences become U+FFFD with `invalid_utf8`; lines are newline-delimited byte records (R-LIM-18).

**Non-goals**
- Does not cover the named query view set (US-06).
- Does not cover pin/unpin retention (US-11).
- Does not invent a second identity system for TTY-class console (US-16 requires the same fetch class).

**Why this framing works**
G8 plus R-FET-8 are the design pressure: if fetch takes paths, the agent is coupled to the evidence layout and R-INV-1 is at risk. This story is the “next retrieval operation” promised by US-01.

**Remaining assumptions**
- Abandoned artifacts are fetchable by default until retention expiry or collection (R-ART-3; closed §25 Q6).

---

### US-06 — Ask the same historical questions no matter how evidence is stored

**JTBD:** When I need structured history (recent runs, events, artifacts, provenance), I want a stable named view with paging, so I can query without learning a storage language or depending on a particular cache.

**Semantic contract**
- Actor: coding agent (MCP `query`); operator may use CLI SQL only on capable backends
- Situation: I need a read over run history after or independent of a single fetch
- Motivation: bounded, backend-independent query (G7)
- Progress: two backends return identical semantic payloads (modulo `backend` and `as_of`) for the same view+params

**Acceptance criteria**
- [ ] MCP `query` is view + params only; raw query languages are not the portable contract (R-QB-21, R-QB-3, R-QB-1).
- [ ] v0.1 views: `run`, `last_error`, `run_tail`, `run_events`, `recent_runs`, `recent_failures`, `run_provenance`, `run_artifacts`, `artifact_refs`. `plugin_stats` is not a v0.1 view (§12). The set is sufficient; TTY completeness is `limits_exceeded` plus fetch, not a tenth view.
- [ ] Until `evidence_finalized`, `query` may only serve `run` and `recent_runs`; other views return `projection.not_finalized` (R-QB-28).
- [ ] Envelope is `{items, next_cursor, truncated, backend, as_of}`; cursors are opaque; stale cursor → `cursor_expired` (R-QB-27). Specified ordering matches R-QB-26.
- [ ] Every collection API bounds item count and bytes; truncation is signaled, never silent (R-QB-23, R-QB-24). Per-cell truncation default 512 bytes (R-QB-25).
- [ ] Filesystem backend ships, is default and reference oracle (R-QB-10–14). Partial backend implementation fails startup (R-QB-4). Backend failure does not fail runs; filesystem fallback (R-QB-5–9). Query is not on the write path.
- [ ] Recovery does not require the query backend (R-STORE-20).
- [ ] SQLite is specified and must pass conformance before advertising; it may follow filesystem and is not a gate for M1–M4 or G1 (R-QB-15).
- [ ] CLI `trestle query --sql` is allowed on capable backends, bounded; it is not the MCP portable contract (R-QB-22).

**Non-goals**
- Does not cover fetch windows (US-05).
- Does not cover live tail of a still-running run (v0.1 no; R-QB-28, §20, R-TTY-9).
- Does not require `plugin_stats` (§12; volume protection is `backend_scan_limit`).

**Why this framing works**
G7 forbids baking DuckDB/SQLite/SQL into the agent grammar. The design effect is: named views first; backends as caches; filesystem as oracle.

**Remaining assumptions**
- None that block this story. A new view is a named requirements amendment.

---

### US-07 — Add a tool by dropping a Python file, not by editing the server

**JTBD:** When I have a new local capability whose dependencies already exist in the environment, I want writing one plugin file to become a callable tool without a restart, registry edit, hand-written schema, or MCP plumbing, so I can extend Trestle at the plugin surface.

**Semantic contract**
- Actor: plugin author
- Situation: adding or replacing a plugin under the watched `plugins/` tree
- Motivation: extend the tool surface without touching the server (G3)
- Progress: after the filesystem change is observed, a `run()` of the new snapshot can be admitted in under 2 seconds when deps are present

**Acceptance criteria**
- [ ] Adding capability should mean writing one Python file and must not require a restart, registry edit, hand-written schema, or MCP plumbing (G3).
- [ ] Schema comes from type hints; description is the first docstring line; version is required semver (R-PLUG-1–3). One canonical path: annotation → type AST → JSON Schema → schema hash (R-PLUG-22).
- [ ] Watch `plugins/` debounced 250 ms; validate → snapshot → publish → increment `registry_version` (R-REG-1, R-REG-2). Atomic pointer swap to an immutable version record (R-REG-3).
- [ ] Write-to-callable under 2 seconds when dependencies are already available; boundary is filesystem change observed → successful `run()` admission (R-G3-1, R-REG-7). Missing third-party package is `import_failed` / validation failure, not a latency miss (R-PLUG-20, R-G3-1). No auto-install (R-PLUG-21).
- [ ] Validation in a throwaway subprocess; snapshot only after validation; failure leaves previous version serving; `list_plugins(invalid=True)` (R-PLUG-16–19, R-REG-5).
- [ ] `tools/list_changed` is an optimization; correctness is that a client that refreshes `tools/list` eventually sees the new publication (R-REG-2). `tools/list` carries `registry_version`; `ttlMs` is never correctness even if a later pin grows it (R-REG-6).
- [ ] Plugin count does not grow the core MCP tool family (R-MCP-1). Promotion has an 8192-byte budget and refuses when exhausted (R-MCP-5–8).
- [ ] Plugin code uses the `trestle` Context surface, not service internals (R-PLUG-6). Wrappers and children must not import FastMCP (R-FMC-3). Child Context wraps, does not subclass, FastMCP’s Context (R-FMC-5).

**Non-goals**
- Does not cover executing from immutable snapshots once admitted (US-09) beyond “publish then run.”
- Does not cover TTY-class as required core machinery (R-TTY-1, G9 vs G3): optional TTY-class publishes like any other plugin when shipped (M6 note) and is not an M1 gate.
- Does not cover nested plugin composition with telemetry linkage (§2.4).

**Why this framing works**
G3 is a distinct job from G1: the author is hiring speed-of-extension, not projection. Hot reload plus snapshot identity (US-09) must not be collapsed, or “edit the file while a run is in flight” becomes undefined.

**Remaining assumptions**
- Quiet-wrapper policy is one wrapper per published snapshot (R-EXEC-34).

---

### US-08 — Keep the control plane usable when a plugin is naive or hostile

**JTBD:** When a plugin prints in a loop, emits a huge result, or crashes the worker, I want Trestle’s admission and tool surface to stay responsive inside the declared envelope, so I can still cancel, refuse excess work, and inspect bounded projections.

**Semantic contract**
- Actor: coding agent (beneficiary); naive/hostile plugin is the circumstance, not the customer
- Situation: framework-observable plugin output or worker death under the hostile-plugin envelope (§19.1 / R-VER-2)
- Motivation: contain consequences of naive plugins (G2) without claiming to control plugin behavior
- Progress: control plane remains responsive; agent context is not floodable by unbounded streams; a crashed child does not crash the server or unpublish tools

**Acceptance criteria**
- [ ] Framework prevents framework-observable plugin output from exceeding declared service-level bounds; under the hostile-plugin test the control plane remains responsive (G2 precise claim; R-VER-2).
- [ ] Capture path enforces per-stream byte limits, counts suppressed bytes, keeps reading and discarding on exhaustion (R-EXEC-10–14, R-LIM-3, R-LIM-4, R-LIM-6). Console: first 25% + last 75% + elision marker (R-LIM-7).
- [ ] Token-bucket for events: drop-with-counting, never block (R-LIM-6, R-LIM-15, R-LIM-16).
- [ ] Over `max_result_bytes` → `result_too_large`; building a huge object may OOM the child (`crashed`); the byte cap is not a memory bound (R-LIM-9, R-LIM-11).
- [ ] A crashed child must not, by itself, crash the server, unpublish tools, or prevent admission of other in-limit runs (R-EXEC-23). Repeated crashes emit `supervisor_unstable` without halting the service (R-EXEC-24).
- [ ] Containment is automatic and silent toward the agent; warn in the log, never in the agent’s face (G2 vs G3).
- [ ] Execution-level containment is not promised: a plugin may exhaust its own worker memory or CPU (G2). Host-level exhaustion outside the managed envelope, writes outside the managed namespace, descendants, direct network, and host manipulation are outside v0.1 guarantees (G2, R-LIM-14, §2.5).
- [ ] Over `max_work_bytes` → `work_limit_exceeded` + cancel (R-LIM-13). Unmanaged disk consumption is observed, not prevented (§9.4 / G2).
- [ ] In v0.1 no run may block on another run (R-EXEC-28). `max_active_runs` and per-plugin concurrency apply (R-EXEC-25–26).

**Non-goals**
- Does not make Trestle a security sandbox (§2.3, §21).
- Does not guarantee other concurrent runs remain healthy (G2).
- Does not cover operator recovery after host process death (US-10).

**Why this framing works**
G2 is easy to over-read as “the machine stays healthy.” The story pins the only v0.1 service promise: control-plane responsiveness inside the declared envelope. Design must therefore bound MCP-visible streams and isolate plugin code from the server process (US-12).

**Remaining assumptions**
- Numeric limits are operational defaults unless marked semantic invariants (R-SCOPE-6, §Invariants vs defaults). Hostile envelope details live in R-VER-2 / “as v0.3.”

---

### US-09 — Run the bytes that were admitted, not the file that was edited later

**JTBD:** When I start a run, I want it to execute from an immutable snapshot captured at creation, so later edits to the plugin file cannot change what that run means.

**Semantic contract**
- Actor: plugin author and coding agent (shared reliability job)
- Situation: plugin source on disk may change after a run is created
- Motivation: provenance of execution identity, not of unmanaged side effects
- Progress: RunSpec records `snapshot_id`; the worker imports that snapshot; identity hashes are recorded; a run identity is not claimed as bit-for-bit reproducibility of the universe

**Acceptance criteria**
- [ ] Registration copies source into `snapshots/<snapshot_id>/` from `source_sha256`; PluginVersion executes from immutable bytes, never `plugins/foo.py` (R-ID-1, R-ID-2). Workers import from the snapshot (R-ID-3).
- [ ] Run captures `snapshot_id` at creation (R-REG-4, R-ID-4). Discovery path is mutable metadata, not identity (R-ID-7). Snapshot store is authoritative registry state (R-ID-6).
- [ ] Every run records plugin, version, hashes, snapshot, args_hash, Python version, platform (R-ID-9). `args_hash` is sha256 of canonical serialization (R-ID-10). `created` includes `spec_hash` and `service_epoch` (R-RUN-19).
- [ ] A run identity is not a reproducibility identity (R-ID-14). Provenance is source and interface only (R-ID-8). Documentation and `describe_plugin` must not claim provenance over unmanaged side effects (R-SCOPE-2).
- [ ] Re-registering `(name, version)` with new source is permitted → new snapshot, `version_source_changed` (R-ID-11). `run` optional `version`; omitted = latest, resolved identity recorded (R-ID-12).
- [ ] Retain snapshots referenced by retained runs (R-ID-5, R-OPS-1–4).
- [ ] `ArtifactRef` resolves before invoke or `artifact_not_found` / `expired` / `missing`; resolution recorded in RunSpec; resolved artifacts are read-only; `Path` must not accept artifact handles (R-PLUG-11–14, R-PLUG-23).

**Non-goals**
- Does not cover `InputFile` hashed external inputs (deferred, §20).
- Does not cover pinning artifacts (US-11).
- Does not claim TTY-class unmanaged host effects have provenance (R-SCOPE-2, US-19).

**Why this framing works**
G3 vs immutability is an explicit conflict: discovery is mutable; execution is from snapshots. This story is why “hot reload” cannot mean “mutate the running child.”

**Remaining assumptions**
- Canonical serialization details for `args_hash` / budgets are in §10 (R-BUD-1–3).

---

### US-10 — After a crash, reconstruct truth from the ledger, not from caches

**JTBD:** When the service process dies mid-run, I want startup recovery to freeze evidence and mark interrupted work from `ledger.ndjson`, so I do not lose written evidence or invent a successful terminal from `meta.json`.

**Semantic contract**
- Actor: operator restarting Trestle (agent later reads the same recovered runs)
- Situation: process death, torn writes, or leftover workers from a previous service epoch
- Motivation: durability of the authoritative record (G4, G5, §5.6)
- Progress: every run dir is either swept, recovered to exactly one `interrupted` (or recorded terminal), or rematerialized; a second recovery pass is a no-op

**Acceptance criteria**
- [ ] `ledger.ndjson` is authoritative; `meta.json` is not consulted for recovery (R-RUN-8). Materialized/derived data is reconstructible from authoritative sources (R-STORE-5).
- [ ] On startup, any run whose ledger lacks a terminal transition is recovered per §5.6 (R-RUN-3). New `service_epoch` (R-STORE-18).
- [ ] Recovery suffix: resolve artifacts; sweep tmp/partial; freeze evidence; append `evidence_finalized` (`partial` unless completeness proven); append exactly one `interrupted`; fsync (R-STORE-18, R-RUN-16). After `evidence_finalized`, no evidence mutation (R-RUN-17).
- [ ] Idempotent: second pass sees terminal and appends nothing; tested (R-STORE-19, R-STORE-21). Recovery must not require the query backend (R-STORE-20).
- [ ] Workers whose `service_epoch` ≠ current are orphans: reap; `stale_worker` if unconfirmed; never adopt (R-STORE-25).
- [ ] Readers tolerate a truncated trailing NDJSON line (R-STORE-10). Atomic write protocol for named files (R-STORE-8, R-STORE-24).
- [ ] CLI includes `recover`; `doctor` includes epoch and drain (R-OPS-5–9).

**Non-goals**
- Does not cover graceful drain on SIGTERM while the process is still alive (US-14).
- Does not cover GC/retention policy except that recovery freezes evidence (US-11).
- Does not resume `interrupted` runs automatically (R-RUN-5).

**Why this framing works**
If design treats `meta.json` or a query index as truth, G4/G5 fail after crash. The table in R-STORE-18 is the job’s acceptance oracle.

**Remaining assumptions**
- Chaos cases listed in R-VER-3 (recovery twice, torn events, SIGTERM drain, etc.) are the verification expression of this story plus US-14.

---

### US-11 — Keep a needed artifact after the producing run would otherwise be collected

**JTBD:** When an artifact still matters after its producing run’s default retention, I want to pin it by handle, so garbage collection cannot delete reachable bytes I still need.

**Semantic contract**
- Actor: coding agent (MCP `pin`/`unpin`) and operator (CLI `pin`/`unpin`)
- Situation: an artifact is available (or abandoned) and must outlive producer-run deletion
- Motivation: reachability-based retention, not hope
- Progress: pin state lives in `$TRESTLE_HOME/pins.json`; unpin of unpinned is a success no-op; collected artifacts cannot be resurrected

**Acceptance criteria**
- [ ] `pin`/`unpin` exist on CLI and MCP. State in `$TRESTLE_HOME/pins.json`, not a query backend (R-ART-14).
- [ ] Pins survive producer-run deletion. Abandoned MAY be pinned. Collected MUST NOT; no resurrection (R-ART-15). `unpin` of unpinned is success no-op (R-ART-16).
- [ ] Reachability, not refcounting: references are ArtifactRef resolution, `ctx.retain`, pin — in spec/ledger or `pins.json` (R-ART-9, R-ART-10). Queries/CLI/content mentions are not references (R-ART-11).
- [ ] Reachable if a referencing run is retained or pinned (R-ART-12). Collection keeps the record (`artifact_expired`) (R-ART-13).
- [ ] GC respects reachability and pins; retain referenced snapshots; sweep orphans/tmp (R-OPS-1–4). Abandoned default retention 24 h (R-ART-5) unless pinned.
- [ ] Files under artifacts/ with no declared record are orphans → sweep (R-ART-4). Abandoned retained, fetchable, not swept as orphans (R-ART-3).

**Non-goals**
- Does not cover artifact staging/promotion during a live run (US-13).
- Does not cover query `artifact_refs` view semantics beyond reachability (US-06).

**Why this framing works**
G4 retain-vs-surface does not by itself keep bytes forever. Pin is the agent-visible job for “I still need this handle.” Design must not put pin state in a disposable query cache.

**Remaining assumptions**
- Default retention numbers (180d metadata / 7d artifacts / 10 GB cap) are operational (R-OPS-1–4).

---

### US-12 — Never run plugin code in the process that talks MCP

**JTBD:** When a plugin runs, I want it isolated in a fresh child behind a quiet wrapper, so plugin imports, native thread pools, and fd 1/2 floods cannot share fate with the FastMCP server.

**Semantic contract**
- Actor: operator (reliability of the control plane); coding agent as beneficiary
- Situation: every admitted run (spawn-per-run)
- Motivation: G2 + R-INV-1 + M0.5 spawn-per-run decision
- Progress: server never runs plugin code; wrapper never imports plugin snapshots; each run is a new child, never forked from a warmed numeric interpreter

**Acceptance criteria**
- [ ] Plugins execute in a child, never in the server or wrapper (R-EXEC-1). Evidence data must not pass through the server process (R-EXEC-13).
- [ ] Wrappers created with `spawn`; forking the FastMCP server is unsafe (R-EXEC-2). Wrapper is single-threaded: no Python threads, no asyncio, no listening sockets; poll/select reactor only (R-EXEC-3).
- [ ] Each run executes in a freshly spawned child; children not reused; child interpreter not created by `fork()` from a process that has imported plugin dependencies (R-EXEC-4, R-EXEC-5). v0.1 wrappers must not import the plugin snapshot or its third-party dependencies (R-EXEC-6). Paying import cost per run is accepted.
- [ ] Child fd 1 and 2 redirected to pipes drained by the wrapper’s single-threaded poll reactor, not a wrapper thread (R-EXEC-10). fd-level interception captures C extensions, grandchildren, and raw fd 1 writes (R-EXEC-12).
- [ ] Each child is its own process group leader (R-EXEC-8). Cancel/timeout terminate the process group and verify no known descendant remains; escapes → `orphaned_process` + wrapper recycle (R-EXEC-19, R-EXEC-9).
- [ ] `def` and `async def` supported; the child owns the loop (R-EXEC-29, R-PLUG-5). FastMCP is not imported in wrapper/child (R-FMC-3). FastMCP is not the executor (R-FMC-2). Transport is standalone stdio in the server process only (R-FMC-1); must not mount via `http_app()` (R-FMC-4).
- [ ] Quiet wrappers: one process per published snapshot; idle reap; supersession drain not kill (R-EXEC-7, R-EXEC-34, R-REG-8).
- [ ] Plugin is testable with stub `Context` (R-PLUG-4) — author can unit-test without the server.

**Non-goals**
- Does not cover TTY-class process topology (requirements explicitly leave mechanisms to design; §22 handoff).
- Does not cover warm-import supervisors (deferred until imports are OS-single-threaded, §20).
- Does not cover true OS containment (cgroups/namespaces; §21).

**Why this framing works**
This is the design-forcing job behind R-INV-1 and G2: projection is not enough if plugin code shares the MCP process. M0.5 is already a requirement-level architecture constraint (spawn-per-run), not a suggestion.

**Remaining assumptions**
- Idle reap seconds (`supervisor_idle_s` 300) are operational; topology is R-EXEC-34.

---

### US-13 — File plugin outputs under the run without giving the plugin the evidence directory

**JTBD:** When I write logs, subprocess output, a return value, or an artifact, I want those bytes filed under my run through Context and framework capture, so I cannot (and need not) write the evidence namespace myself.

**Semantic contract**
- Actor: plugin author (primary); coding agent consumes the filed evidence
- Situation: plugin execution with `Context`
- Motivation: G5 — every framework-observable output filed under the request, partitioned by type
- Progress: managed outputs go through Context / pipes; `work/` is the only writable plugin namespace; artifacts promote atomically into evidence

**Acceptance criteria**
- [ ] Writable plugin namespace is disjoint from evidence; `ctx.tmp` inside `work/`; `ctx.artifact()` returns a path under `work/artifact-staging/`, never `evidence/`; traversal rejected (R-STORE-1, R-STORE-2). Framework does not pass any writable evidence path to plugin code (R-STORE-3).
- [ ] `ctx.artifact()` records `declared` immediately; finalization resolves every declared artifact to available or abandoned (R-ART-1, R-ART-2, R-RUN-18). Registration promotes staging with fsync → rename → fsync dir and appends `artifact_available` (R-STORE-22).
- [ ] `ctx.log` writes `events.ndjson` directly; root logging handler installed; handler removal → `logging_hijacked` but `ctx.log` still works; not a run failure (R-CTX-1, R-CTX-5).
- [ ] `ctx.run_cmd` captures via the wrapper pump; returns tails capped at 2 KB for plugin logic; children in the run process group (R-CTX-2, R-CTX-3). Ordinary commands that do not require a TTY continue on this pipe-capture path (R-TTY-8). Stdlib subprocess that stays in-group and inherits fds is also managed console (R-AUTO-5); it is not required that authors call `run_cmd` for capture.
- [ ] Child owns `events.ndjson`, `result.json`, `result.index`, `work/*`; wrapper owns `console/*`; server owns spec/ledger/meta/summary/index/pins/epoch (R-STORE-6). On child death, unfinished files transfer to the wrapper for finalization only (R-STORE-7).
- [ ] `work/` removed at finalization unless non-success and `keep_work_on_failure` (R-STORE-4). All Context output subject to §9 (R-CTX-4).
- [ ] `copy_artifact` must not return an evidence path (R-CTX-6). Artifact bytes immutable after `artifact_available` (R-PLUG-24).
- [ ] `effects` are hints, never enforced (R-PLUG-15). Unmanaged side effects (absolute `open()` outside `work/`, env, network, session-escaping subprocess) are possible, not prevented, not recorded (§2.5, R-AUTO-7).

**Non-goals**
- Does not cover agent fetch/query of what was filed (US-05, US-06).
- Does not cover the automatic cwd / `outputs/` / tempfile steering (US-23).
- Does not cover TTY-class capture topology (US-16) except that unmanaged TTY bytes have no provenance theater (US-19).
- Does not cover nested calls’ telemetry linkage (§2.4).

**Why this framing works**
G5 plus namespace split is the author-facing contract: Trestle can only promise what it can observe. Design that hands the plugin `evidence/` writable paths would break R-STORE-3 and R-INV-1.

**Remaining assumptions**
- Oneshot TTY-class evidence files through existing Context sinks; completeness is status-frame `limits_exceeded` plus fetch, not a new view.

---

### US-14 — Shut down without lying about in-flight work

**JTBD:** When I send SIGTERM/SIGINT, I want the service to stop admitting new work, finish or cancel running children under a grace period, and exit with durable evidence, so a restart does not see a healthy-looking half-run.

**Semantic contract**
- Actor: operator
- Situation: service shutdown
- Motivation: draining is a service state, not a run state
- Progress: new runs get `service_draining` as request outcomes; running work is finalized or cancelled; wrappers terminate; query ingest stops at watermarks and does not block shutdown

**Acceptance criteria**
- [ ] SIGTERM/SIGINT → `draining`: stop new runs (`service_draining`); reject queued-not-started as admission outcomes; allow running children until `shutdown_grace_s` (30); cancel remainder; finalize; fsync; terminate wrappers; exit (R-EXEC-32).
- [ ] In-flight validation is abandoned (previous PluginVersion remains). Query ingestion stops at watermarks and must not block shutdown (R-EXEC-33).
- [ ] Every run carries an absolute deadline set at admission (R-EXEC-21).
- [ ] `doctor` includes epoch and drain (R-OPS-5–9).

**Non-goals**
- Does not cover crash recovery when the process never drained (US-10).
- Does not cover interactive TTY control via signals-as-agent-verbs (deferred; R-TTY-9).

**Why this framing works**
Vocabulary insists draining ≠ run state. If design maps drain onto `cancelled` without a request-outcome path for not-yet-started work, US-02 is violated.

**Remaining assumptions**
- Default grace numbers are operational.

---

### US-15 — Get a small, honest summary of a huge return value

**JTBD:** When a plugin returns a large structured value, I want a deterministic summary that never truncates strings mid-field and never slurps the full result into the server, so I can still fetch the rest by handle.

**Semantic contract**
- Actor: coding agent
- Situation: plugin return value exceeds `summary_budget` or is huge on disk
- Motivation: G1/G4 projection without lying about shape
- Progress: summary built from `result.index` + ranged reads; footer names handle, bytes, truncated, omitted; algorithm is deterministic and tested

**Acceptance criteria**
- [ ] If the value fits `summary_budget`, return verbatim (R-BUD-5). Else: object field-skip; array `{count, sample, handle}`; scalar handle only (never truncate strings); `None` always fits; nested objects top-level only (R-BUD-6). Add whole fields in `summary_fields` order; never partial fields (R-BUD-7).
- [ ] Footer: handle, `result_bytes`, `truncated`, `omitted` (R-BUD-8). Deterministic; tested (R-BUD-9). `summary_fields` on non-object rejected at registration (R-BUD-10).
- [ ] Server middleware enforces the budget; child cannot raise the cap (R-BUD-11). No universal envelope (R-BUD-12).
- [ ] Child writes `result.index` during streaming serialization; index bounded (`max_index_bytes` 64 KB) else coarsen + `index_truncated` (R-BUD-22, R-BUD-23). Server builds summary from index + `pread`; never slurp; child death mid-write → `result_state=invalid` (R-BUD-24).
- [ ] `result_state ∈ {absent, complete, invalid, too_large}` (R-STORE-13). Canonical JSON rules including NaN/Infinity → `non_canonical_value` (R-BUD-1, R-BUD-2).
- [ ] `summary.json` records exact bytes + hashes + algorithm versions (R-BUD-21). Computed `minimum_envelope_bytes`; reject smaller budgets (R-BUD-18–20).

**Non-goals**
- Does not cover console stream elision (US-08 / R-LIM-7).
- Does not cover query views (US-06).

**Why this framing works**
M0.6 is already accepted at requirements level: huge `result.json` must still yield a tiny summary. Design that loads the result into the FastMCP process fails R-LIM-10 and R-INV-1.

**Remaining assumptions**
- Default 256 MB result cap is operational (R-LIM-1) except too-large behavior (R-LIM-9 invariant).

---

### US-16 — Complete TTY-class oneshot work on the same surface — or see that I must leave

**JTBD:** When my local work’s observable behavior depends on a TTY, I want to start, wait until terminal, and retrieve the same evidence class as any other run — or else see a named exclusion that this install cannot do that work — so I am never stuck in a silent hole and never taught a second product.

**Semantic contract**
- Actor: coding agent
- Situation: a job that is TTY-class (not merely “a terminal run state”)
- Motivation: G9 / R-REACH-1 — reach TTY-class local work without making it required core
- Progress: either a published TTY-class capability completes a named oneshot through the existing nine tools, or unavailability is agent-visible with leave-Trestle guidance

**Acceptance criteria**
- [ ] The system either (a) lets the agent complete TTY-class oneshot local work through existing run / wait / query / fetch, same identity and evidence class as any other run, or (b) when the optional capability is not installed, presents a named, agent-visible exclusion that TTY-class work is unavailable and the agent must leave Trestle. Silence satisfies neither (R-REACH-1, R-TTY-5).
- [ ] v0.1 MAY include the capability; when present it MUST be advertised through existing plugin discovery. Ordinary POSIX core (M1–M7) remains complete when it is not installed. TTY-class work is not required core machinery and is not an M1 exit requirement (R-TTY-1, G9 vs G3).
- [ ] When advertised and ready: same handle grammar, same named views, same fetch windows; agent-visible tool family does not grow (R-MCP-1, R-TTY-2). Completeness is framework-observable bytes plus counted suppressions, retrievable after restart by the same query and fetch class (R-TTY-2).
- [ ] Requirement sentences name outcomes. Deleting a particular helper must not require a new agent-visible grammar, a new MCP tool, or an amendment to R-INV-1 or R-MCP-1. Agents must not be taught a vendor session identifier. Untrusted helper bytes enter the MCP process only as bounded projection (R-TTY-7).
- [ ] Oneshot means start, wait until terminal, retrieve — not interactive stdin/resize/signal-as-verbs, not live tail of a running run, not agent-delegation-as-primary (R-TTY-9, §2.3, §20).

**Non-goals**
- Does not cover ordinary non-TTY commands (US-13 / R-TTY-8).
- Does not cover unreadiness-vs-absence-vs-failed-run distinguishability details (US-18).
- Does not cover counted-gap completeness (US-17).
- Does not name or require any vendor helper (R-TTY-7, §22).

**Why this framing works**
G9 is a visibility job as much as a capability job. Design that ships neither advertisement nor exclusion fails R-REACH-1 even if the core ledger works. Design that adds a tenth MCP tool fails R-TTY-2/R-MCP-1.

**Remaining assumptions**
- Named proving class is programs whose behavior depends on `isatty` (closed §25 Q8). Shipping a plugin is not required for R-REACH-1.
- How a terminal is provided remains design (R-TTY-7).

---

### US-17 — Never treat dropped TTY-class bytes as a complete success

**JTBD:** When TTY-class capture drops, wraps, or fails to count bytes, I want that gap to be a first-class countable fact on the run, so I do not treat a finished-looking projection as complete evidence.

**Semantic contract**
- Actor: coding agent interpreting a TTY-class run after `evidence_finalized`
- Situation: capture hit a limit or otherwise lost bytes during TTY-class oneshot
- Motivation: G9 vs G4 — completeness is recorded bytes plus counted gaps
- Progress: absence of a gap record means no gap was observed, not that gaps were not checked; a run that “succeeds” with holes remains classifiable as incomplete

**Acceptance criteria**
- [ ] TTY-class console evidence must not look complete while bytes were dropped, wrapped, or uncounted. Every gap is a first-class countable fact. Absence of a gap record means no gap was observed, not that gaps were not checked (R-TTY-3).
- [ ] Forcing a known loss during TTY-class capture yields a queryable incompleteness record (counts and `completeness` partial or equivalent). A projection that appears finished with uncounted loss fails (R-TTY-3).
- [ ] A run that “succeeds” with holes remains classifiable as incomplete under existing completeness rules, not as silent success (R-TTY-4). `limits_exceeded` appears in meta, ledger, and status frame (R-LIM-5). Markers record stream, limit, bytes recorded, bytes suppressed (R-LIM-3).
- [ ] `evidence_finalized` records `completeness ∈ {complete, partial}` (R-STORE-14).

**Non-goals**
- Does not cover admission refusal when capability is missing (US-18).
- Does not cover wrapper console elision for ordinary pipes except as the same limit-marker invariants (US-08).
- Does not specify on-disk gap encoding (design; §22).

**Why this framing works**
This is the G4 overlay for TTY-class: the danger is theater of completeness. It is a separate job from “can I run TTY work at all” (US-16).

**Remaining assumptions**
- Completeness is queryable on the terminal `RunView.limits_exceeded` (and ledger `completeness`); not a new ViewName (closed §25 Q5).

---

### US-18 — Tell “not installed,” “not ready,” “invalid plugin,” and “ran and failed” apart

**JTBD:** When TTY-class work cannot proceed, I want the failure story to match the moment it failed, so I do not confuse a missing install, a helper that is advertised but not ready, a source that failed validation, and a run that actually executed.

**Semantic contract**
- Actor: coding agent
- Situation: TTY-class capability absent, advertised-but-not-ready, plugin source invalid, or post-admit execution failure
- Motivation: R-TTY-4, R-TTY-5, R-TTY-6, R-REG-5 kept in their original meanings
- Progress: I can choose leave-Trestle vs retry-later vs fix-plugin vs inspect-run-evidence without a fake `run_id`

**Acceptance criteria**
- [ ] If the TTY-class capability is missing or not ready at admission, the request is refused: no `run_id`, no ledger row that pretends the work ran (R-TTY-4, R-RUN-1, R-MCP-4).
- [ ] If the request was admitted and the work then fails, the outcome is a run in a terminal state; evidence written before failure is retained (R-TTY-4, R-EXEC-20).
- [ ] When not installed, unavailability is observable via plugin discovery and/or describe/`run` refusal without implying a completed run (R-TTY-5).
- [ ] When advertised but not ready, Admit returns `admission.tty_not_ready` with no `run_id`, distinct from (a) plugin source validation failure (R-REG-5) and (b) a failed run. R-REG-5 must not be reinterpreted as helper unreadiness (R-TTY-6).
- [ ] `list_plugins(invalid=True)` / previous-version-kept remains the meaning of R-REG-5 (R-TTY-6, R-PLUG-19).

**Non-goals**
- Does not invent a catalog health cell; unreadiness is `admission.tty_not_ready` (R-TTY-6).
- Does not cover counted gaps on an admitted run (US-17).

**Why this framing works**
Four different operator/agent responses. If design overloads `import_failed` or `invalid=True` for helper health, hot reload (US-07) and TTY reach (US-16) corrupt each other.

**Remaining assumptions**
- None that block this story. Channel is `admission.tty_not_ready`; `valid` stays R-REG-5.

---

### US-19 — Keep ordinary commands on pipes; do not fake provenance for unmanaged TTY effects

**JTBD:** When I run a command that does not need a TTY, I want the existing pipe-capture path, and when TTY-class work never entered managed capture, I want no claim that Trestle provenance covers it, so enabling TTY-class does not silently reroute the default world or invent an audit trail.

**Semantic contract**
- Actor: plugin author (default path) and coding agent (trust in `describe_plugin` / docs)
- Situation: mixed ordinary POSIX commands and optional TTY-class capability on one install
- Motivation: R-TTY-8, R-SCOPE-2, G9 vs G8
- Progress: non-TTY commands still complete with pipe-captured stdout/stderr as R-CTX-2; docs do not claim unmanaged TTY side effects are filed

**Acceptance criteria**
- [ ] Ordinary commands that do not require a TTY continue to use the existing pipe-capture execution path. TTY-class execution must not become the default route for those commands (R-TTY-8, R-CTX-2).
- [ ] Documentation and `describe_plugin` must not claim provenance over unmanaged side effects. TTY-class work that never entered the managed evidence path remains unmanaged; v0.1 must not invent provenance theater for it (R-SCOPE-2).
- [ ] Descriptions state side effects; they do not restate schema types (R-MCP-3).

**Non-goals**
- Does not cover how TTY-class capture is implemented when it *does* enter Context/framework capture (US-13, US-16).
- Does not cover interactive TTY (R-TTY-9).

**Why this framing works**
G9 must not cannibalize the M1–M7 pipe core. Provenance theater would violate G8 (agents taught a false implementation story).

**Remaining assumptions**
- Classification of “requires a TTY” vs not is a design/plugin-contract concern; requirements only forbid making TTY-class the default route.

---

### US-20 — Retry the same work without creating a conflicting second run

**JTBD:** When my client retries a `run` after a transport blip, I want an idempotency key to reattach to the same admitted work or to refuse a conflicting reuse, so I do not double-execute and I can reconstruct the key after restart.

**Semantic contract**
- Actor: coding agent (and MCP client library)
- Situation: retry of `run` within TTL with the same or different plugin/snapshot/args
- Motivation: reliable execution under G2/G6 without hidden session state
- Progress: matching key+plugin+snapshot+args_hash returns the existing run; different values → `idempotency_key_conflict` with no new conflicting execution; keys reconstructible from spec/ledger across restart

**Acceptance criteria**
- [ ] Idempotency key + TTL 3600; match plugin+snapshot+args_hash; conflict if reused with different values; args-hash dedup opt-in (R-WAIT-10–12).
- [ ] Keys reconstructible from spec/ledger across restart (R-WAIT-13).
- [ ] Conflict is a request outcome with no `run_id` (R-MCP-4).
- [ ] State travels as explicit handles, never hidden session state (R-SCOPE-4).

**Non-goals**
- Does not cover `await_runs` join semantics (US-04).
- Does not cover plugin-level `idempotent=False` decorator default except as plugin metadata (decorator defaults, §7).

**Why this framing works**
Without this job, wait (US-04) and crash recovery (US-10) still leave the client able to duplicate side effects. It is a reliability job, not a workflow engine (§2.3).

**Remaining assumptions**
- Opt-in args-hash dedup without a client key is specified as opt-in (R-WAIT-10–12); default policy beyond TTL is design.

---

### US-21 — Read errors that say what to do next, without a traceback in the tool result

**JTBD:** When something goes wrong, I want a stable error code, a short message, an origin class, and an honest `retryable` bit, so I can branch without scraping stack traces out of the MCP payload.

**Semantic contract**
- Actor: coding agent
- Situation: admission, plugin, execution, storage, or framework error
- Motivation: G1 (compact) + G8 (no implementation dump)
- Progress: traceback lives in events, not in the default status frame; plugin codes are namespaced

**Acceptance criteria**
- [ ] Origin classes: plugin / framework / execution / storage / admission; stable `code`; message <200 chars; `retryable` only when it changes recovery; tracebacks in events only; plugin codes namespaced (R-ERR-1–6).
- [ ] Status-frame errors are compact; no tracebacks (R-BUD-13–17).
- [ ] Plugin exceptions must not map to MCP task `failed` if Tasks exist; plugin exception → task `completed` + `isError`; crash/interrupt → task `failed` (R-MCP-11, R-MCP-12) — only if a Trestle-owned mapper is implemented.

**Non-goals**
- Does not cover `last_error` view contents beyond the query contract (US-06).
- Does not cover `logging_hijacked` except as a non-failing execution event (US-13).

**Why this framing works**
G1 forbids dumping internals; G8 forbids requiring the agent to parse Python tracebacks. `retryable` is constrained so design cannot mark everything retryable.

**Remaining assumptions**
- Full code catalog is “stable `code`” plus examples elsewhere; listing every code is not this story’s job.

---

### US-22 — Operate the ledger from the CLI without the MCP projection rules

**JTBD:** When I am diagnosing the service as a human operator, I want config defaults, a non-stdout service log, doctor/recover/pin, and optional verbose CLI output, so I can keep the agent surface small without blinding operations.

**Semantic contract**
- Actor: operator on the same localhost user
- Situation: install, debug, retain, or recover outside an agent turn
- Motivation: operations (R-OPS) without weakening G1 for agents
- Progress: CLI verbosity is exempt from §10; agent MCP path is not

**Acceptance criteria**
- [ ] `config.toml` with working defaults; `service.log` not stdout; `doctor` includes epoch and drain; CLI includes `pin`/`unpin`/`recover`; CLI verbosity exempt from §10 (R-OPS-5–9).
- [ ] Local-only: loopback, same machine, same user privileges. Plugin authorship is not a privilege boundary. No review gates, signing, credential brokering, egress allowlists, authentication, or multi-tenancy (§2.1).
- [ ] POSIX-only for v0.1 (R-SCOPE-1).

**Non-goals**
- Does not cover the agent MCP nine-tool contract (US-01).
- Does not cover SQL query power-user path except as bounded CLI on capable backends (US-06).
- Does not add remote or multi-user isolation (§21).

**Why this framing works**
G1 is agent-context scarce. Operators still need a louder instrument. Requirements already split these surfaces; design must not force doctor output through the same budget as `run`.

**Remaining assumptions**
- Exact doctor checks beyond epoch and drain are design.

---

### US-23 — Write a larger script and have Trestle file it without extra ceremony

**JTBD:** When I drop a larger Python script as a plugin, I want ordinary prints, logs, relative file writes, tempfile use, and in-group subprocesses to be captured and bounded automatically, so I do not have to remember ledger paths, MCP tools, or projection budgets to do the right thing.

**Semantic contract**
- Actor: plugin / script author
- Situation: one Python file using the standard library plus Context public members
- Motivation: G3 corollary + G5 + G8 — automatic managed path enforced by the child runtime and Context, not by author discipline
- Progress: a naive script that prints, logs, writes `outputs/report.csv`, uses `tempfile`, and `subprocess.run` (no new session) produces a correct run: console in evidence, events filed, keeper files auto-promoted as `art_…` handles, scratch deleted on success, agent sees DefaultAgentSuccess

**Acceptance criteria**
- [ ] Child cwd is `work/`; snapshot dir is not cwd and is not writable (R-AUTO-1, R-ID-2).
- [ ] `TMPDIR` / tempfile roots the child controls point at `work/tmp` (`ctx.tmp`) (R-AUTO-2, R-CTX-7).
- [ ] `work/outputs/` (`ctx.outputs`) exists at start; regular files remaining there at `execution_ended` are auto-declared and promoted as artifacts with the same handle grammar as `ctx.attach` (R-AUTO-3, R-ART-17). `ctx.artifact` / `ctx.attach` remain valid and explicit.
- [ ] Scratch under `work/tmp` and undeclared files elsewhere under `work/` are not auto-promoted and go away with `work/` on successful finalization (R-STORE-4).
- [ ] fd 1/2, stdlib logging, and return-value `result.index` work without author action; §9–§10 bounds apply automatically (R-AUTO-4).
- [ ] Stdlib subprocess that does not start a new session stays in the process group and inherits captured fds; `ctx.run_cmd` is documented, not required for console capture (R-AUTO-5).
- [ ] Authors need not import FastMCP or Kernel types. Stub Context applies the same cwd / TMPDIR / outputs / logging semantics (R-AUTO-6, R-PLUG-4).
- [ ] Absolute `open()` outside `work/`, network, env mutation, and session-escaping subprocesses remain unmanaged; no provenance theater (R-AUTO-7, R-SCOPE-2). Auto-promote honors artifact limits with counted markers (R-LIM-3).

**Non-goals**
- Does not make Trestle a security sandbox (US-08, §21).
- Does not auto-promote the entire `work/` tree (only `outputs/`).
- Does not cover TTY-class helper topology (US-16).

**Why this framing works**
Without this job, G3 is “write one file” but G5 still demands the author remember `ctx.artifact` or lose files when `work/` is deleted on success. The interface — not a style guide — has to steer keepers into `outputs/` and capture the rest.

**Remaining assumptions**
- Auto-promote of a huge `outputs/` tree is bounded by existing artifact limits, not a new quota species.

---

### US-24 — Stay on the script side of the foundation

**JTBD:** When I write or change a plugin, I want a hard wall between my script and Trestle’s control plane, so I cannot accidentally import the ledger, MCP, or Kernel — and so Trestle can change internals without rewriting my file.

**Semantic contract**
- Actor: plugin / script author (and foundation maintainers as the other side of the wall)
- Situation: one plugin file under `plugins/`; foundation lives in `trestle/` server/wrapper/kernel packages
- Motivation: G3 + G8 + R-BOUND — wrap is one-way
- Progress: my imports are PluginSurface only; tests run with stub Context; adding my file does not edit foundation source; changing Kernel ports does not edit my file if Context members are unchanged

**Acceptance criteria**
- [ ] Foundation wraps scripts; scripts do not wrap the foundation (R-BOUND-1). Child process may host both a runtime module and the script; they remain distinct packages.
- [ ] Script imports of Kernel, FastMCP, ledger, query, wrapper, or evidence-path helpers fail validation (R-BOUND-2, R-PLUG-6). Permitted: decorator, `Context`, `ArtifactRef`, documented plugin-facing errors.
- [ ] Scripts cannot call Admit, Execute, Project, or LedgerCommand (R-BOUND-3).
- [ ] Script code runs only in the child; server and wrapper do not import the snapshot (R-BOUND-4, R-EXEC-1, R-EXEC-6).
- [ ] Adding or removing a script does not require a foundation source edit (R-BOUND-5, G3). Foundation internals can change without a script edit if PluginSurface is unchanged.
- [ ] Stub Context is enough to unit-test the script with no Kernel or MCP server (R-BOUND-6, R-PLUG-4).

**Non-goals**
- Does not cover automatic cwd / outputs capture (US-23).
- Does not make plugin authorship a security boundary (§2.1).
- Does not allow scripts to subclass FastMCP Context (R-FMC-5).

**Why this framing works**
US-12 is process isolation; US-23 is automatic filing. Neither names the **import and ownership wall**. Without this story, a “helpful” `from trestle.server import ledger` would collapse the wrap and teach authors the control plane.

**Remaining assumptions**
- Exact `trestle` public package layout is design; the allowlist is PluginSurface, not a directory tree in this story.

---

## Mapping: goals → stories (design pressure)

| Goal | Stories that carry it | Design effect if ignored |
|------|----------------------|--------------------------|
| G1 Minimize context | US-01, US-05, US-15, US-21 | Fat default payloads; string truncation theater |
| G2 Contain naive plugins | US-08, US-12 | Plugin in MCP process; unbounded pipes |
| G3 Extend without touching server | US-07, US-09, US-23, US-24 | Registry edits; live-file execution; authors import Kernel |
| G4 Retain all, surface almost none | US-01, US-10, US-15, US-17 | Silent discard; caches as truth |
| G5 File under the request | US-03, US-13, US-23 | Shared temp dirs; unpartitioned logs; success deletes unsaved files |
| G6 Wait without polling | US-04, US-20 | Turn-per-poll; duplicate runs |
| G7 Bounded named query | US-06 | SQL/path in agent grammar |
| G8 Hide implementation | US-01, US-05, US-16, US-19, US-23, US-24 | Paths, vendor session ids; authors taught the ledger |
| G9 TTY-class optional reach | US-16–US-19 | Silent hole, tenth tool, TTY as default path |

## Mapping: explicit non-goals of v0.1 (do not grow stories)

These are **set-level** exclusions from §2.3–2.4 and §20. No story above treats them as done:

- Security sandbox, distributed scheduler, multi-tenant, workflow engine, remote
- Interactive TTY control; live observation of a still-running run; agent-delegation-as-primary
- Nested calls, linear pipelines, external callbacks, MRTR `input_required`, `InputFile`
- Windows; FastMCP Tasks extra (Docket/Redis); Sampling; v0.1 Tasks mapper (R-WAIT-4)

## Closed decisions (former open questions)

Source items from requirements §25. Binding for v0.1; stories cite them rather than leaving how-to-build unset.

1. **Stdio hold time (US-04).** v0.1 assumes stdio hosts can hold a multi-minute `tools/call`. No Tasks mapper (R-WAIT-4). Docket remains forbidden.
2. **`ttlMs` (US-07).** `registry_version` is correctness even if a later pin grows `ttlMs` (R-REG-6).
3. **Wrapper topology (US-12).** One quiet wrapper per published snapshot (R-EXEC-34).
4. **Volume without `plugin_stats` (US-06).** `backend_scan_limit` + recency cache 500; no stats view.
5. **View set (US-06, US-16, US-17).** Nine views are sufficient. TTY gaps live on `limits_exceeded` / fetch.
6. **Abandoned fetch (US-05, US-11).** Yes, until expiry or collection (R-ART-3).
7. **λ (US-01).** Calibrate at M7; do not block envelopes (R-COST-3).
8. **TTY proving workloads (US-16).** Class = `isatty`-sensitive oneshot. Overlay / absence is complete without shipping a plugin.
9. **Live query of running evidence streams (US-04, US-06, US-16).** No (R-QB-28). Wait with `wait_ms` / `await_runs`.
10. **Unreadiness channel (US-18).** `admission.tty_not_ready`. Catalog class is not health. R-REG-5 unchanged.

**Assumptions that remain risks (not open questions):** automatic runtime steers naive I/O into `work/` and `outputs/`; absolute paths outside `work/` stay unmanaged (US-23 / R-AUTO-7).

**Still design, not a story:** TTY helper process topology (R-TTY-7). On-disk encoding of TTY gap files (Execute observes `work/*`).
