# Trestle — Requirements Specification

**A durable local execution ledger with an MCP control surface.**

| | |
|---|---|
| Status | Draft v0.8 |
| Date | 2026-08-25 |
| Supersedes | v0.7, v0.6, v0.5, v0.4, v0.3, v0.2, v0.1 |
| Scope | system |
| Mode | amend |
| Node | trestle |
| Extension Class | structural (script/foundation seam; TTY overlay; automatic child runtime) |
| Deployment | localhost only, single user |
| Platform | POSIX (Linux, macOS). Windows out of scope for v0.1 |
| Language | Python 3.12+ |
| Protocol | MCP `2026-07-28` via FastMCP |

*"Trestle" is a placeholder codename.*

## Guiding Light

Upstream calls this document must serve. Plain language; no requirement IDs.

### Why this project exists

- Agents need to write and run command-line tools and scripts. Naive output, logs, and returns flood the agent's context and make those tools unusable.
- Those scripts can do powerful work. Trestle keeps that work agent-friendly: contain the flood, keep a record of what happened, and give the agent telemetry it can pull when it needs it — not dump everything into the next turn.
- Trestle is a **foundation that wraps scripts**, not a library scripts must integrate. Script authors write ordinary Python behind a small Context; the foundation owns capture, ledger, and the agent porch.

### This document's job

- State what must be true and why — observable outcomes and constraints, not design shapes.
- Hand off a problem statement complete enough for architecture and interface design to derive how.

---

RFC 2119 keywords. Requirement IDs are stable across revisions; this revision appends IDs and does not renumber. §22 lists changes from v0.6. Spike evidence is in `spikes/RESULTS.md` and §26.

**Framing.** Trestle is not an MCP server that runs plugins. It is a durable local execution ledger with an MCP control surface. The ledger is the truth; the run directory is the evidence plane; workers are disposable machinery; query backends are caches; the agent sees only projections.

**Two planes.** The **foundation** (server, wrapper, child runtime, ledger, projection) wraps **scripts** (one-file plugin callables). Scripts never wrap the foundation: they do not import Kernel or FastMCP, do not speak ledger or MCP, and do not write `evidence/`. The only script-facing contract is PluginSurface (`trestle` public types + `Context` + process environment). Automatic filing (R-AUTO) is foundation behavior on that wrap, not a script obligation.

v0.1 optionally includes **TTY-class local work** as the same evidence and query class as any other run: oneshot capture through the existing agent surface, not a second product. The ordinary POSIX local-work core (milestones M1–M7) remains complete without that capability installed. Helpers that satisfy the capability are deletable means; requirement sentences MUST remain true if any particular helper is removed, and MUST NOT name a vendor.

- **R-INV-1** **No untrusted plugin-controlled byte stream MAY ever cross into the MCP process without first passing through a bounded, deterministic projection boundary.** This applies to return values, stdout, stderr, events, artifact metadata, errors, plugin descriptions, query results, and fetch results.

**Normative vocabulary.** These terms are used in their exact senses below; do not collapse them.

| Term | Meaning |
|---|---|
| **request** | One MCP or CLI invocation. May be refused without creating a run. |
| **run** | The execution of one RunSpec. Exists only after admission. Authoritative state is the ledger. |
| **execution** | The child-process lifetime of a run, from `started` to `execution_ended`. |
| **server** | The FastMCP process: admission, scheduler, ledger, registry, projection, fetch. Never runs plugin code. |
| **foundation** | Server + wrapper + child runtime + ledger + projection. Wraps scripts. Owns capture, bounds, filing, recovery, and the agent porch. |
| **script / plugin** | One Python callable (and its snapshot bytes). User work. Sees PluginSurface only. Never a foundation process. |
| **PluginSurface** | The entire script-facing contract: public `trestle` types, `Context`, decorator, and the child process environment (cwd, `TMPDIR`, fds). Not a Kernel port. |
| **wrapper / supervisor** | A quiet, single-threaded process-control reactor. Does not import plugin code in v0.1. Spawns children; never runs plugin code. |
| **child / worker** | The disposable process that executes one run. Never reused. Hosts the child runtime (foundation) and the script (user). |
| **terminal** | A run state that cannot change. Distinct from a request outcome. Distinct from a TTY. |
| **TTY-class** | Local work whose observable behavior depends on a TTY (not on run-state vocabulary). |
| **evidence** | Framework-owned bytes under `evidence/`. |
| **artifact** | A file produced by a run, identified by an opaque handle. |
| **result** | The plugin's return value, if any, in `result.json`. |
| **summary** | The bounded projection returned to the agent (`summary.json`). |
| **handle** | An opaque identifier. Never a filesystem path on the MCP surface. |
| **view** | A named, bounded, backend-independent query over evidence. |
| **backend** | A derived cache. Never a source of truth. |
| **Task** | An MCP Tasks-extension projection of a run. Not a Trestle primitive. |
| **service epoch** | Identity of one server process lifetime. |
| **draining** | A **service** state, not a run state. |

**Invariants vs defaults.** Numeric limits are **initial operational defaults** unless a requirement states a semantic invariant. Defaults currently include: 512-byte cell truncation, 2 KB subprocess tails, 25/75 console split, 5,000 events/s, 100,000 events/run, 10 GB `work/`, 10 GB total storage, 30 s finalization.

---

## 1. Goals

### G1 — Minimize context consumed per unit of work done

The scarce resource is the agent's context window. Every tradeoff resolves toward fewer agent-visible tokens and fewer agent turns, even when that costs the service more work. Measured per §18, gated per §19. The optimization target is **cost per successfully completed task**, not tokens per interaction.

### G2 — Contain the consequences of naive plugins

A plugin that prints in a loop, shells out to a chatty subprocess, logs a million events, and returns a 4 GB dict **MUST NOT** be able to flood agent context, corrupt the evidence record, or render the Trestle control plane unresponsive under the supported hostile-plugin envelope.

**Precise claim.** The framework contains the *consequences* of plugin behavior over framework-observable outputs (§2.4). It does not control plugin behavior.

- **Control-plane containment is guaranteed under the declared envelope.** The framework MUST prevent framework-observable plugin output from exceeding declared service-level bounds, and under the hostile-plugin test (§19.1) the Trestle control plane MUST remain responsive.
- **Execution-level containment is not.** A plugin MAY exhaust its own worker's memory or CPU; this is worker failure, not service failure.
- **Host-level resource exhaustion outside the managed envelope is outside v0.1's guarantees.** A plugin MAY consume up to `max_work_bytes` in `work/`, write outside the managed namespace, spawn descendants, make direct network calls, and manipulate the host. G2 **MUST NOT** be read as "the host remains healthy" or "every concurrent run remains healthy."
- **Unmanaged disk consumption is observed, not prevented.** §9.4.

Trestle-process survival ≠ host health ≠ other-run health ≠ control-plane responsiveness. Only the last is the v0.1 service promise, and only inside the declared envelope.

### G3 — Extend the tool surface without touching the server

Adding capability **SHOULD** mean writing one Python file, and **MUST NOT** require a restart, registry edit, hand-written schema, or MCP plumbing.

A larger script written as that one file **MUST** get the managed path by default: the child process environment and Context protocol file, bound, and project framework-observable outputs without the author remembering ledger layout, handle grammar, projection budgets, or MCP (R-AUTO-1–7). Automatic is an interface property, not a tutorial obligation.

- **R-G3-1** **For plugins whose dependencies are already available in the Trestle environment, write-to-callable MUST be under 2 seconds**, measured per R-REG-7. A missing third-party package is a validation failure (R-PLUG-20), not a latency miss.

### G4 — Retain all framework-observable evidence, surface almost none

All framework-observable evidence **MUST** be retained up to declared capture and storage limits, with deterministic accounting of anything suppressed. "Complete" is bounded by §9, never silently.

The measurable form of "surface almost none": **default tool results contain only the status frame plus the minimum information needed to identify the next retrieval operation** (§10.3).

### G5 — File every consequence under the request that caused it

Every framework-observable output **MUST** be filed under its run and partitioned by type.

### G6 — Let agents wait without polling

An agent waiting on a five-minute job **MUST NOT** burn a turn per poll interval.

### G7 — Give agents a bounded, backend-independent query interface

Structured, read-only access over run history through a **stable named view set** independent of how evidence is stored (§12).

### G8 — Isolate agents from implementation detail

No agent-visible operation should require the agent to understand how it was implemented.

### G9 — Reach TTY-class local work without making it required core machinery

TTY-class local work MUST sit under the existing evidence and query aperture, or be visibly out of this install — it MUST NOT become a silent hole, and it MUST NOT become required core machinery. When the optional capability is advertised and ready, agents complete that work as oneshot runs of the same class as any other run. When it is not installed, agents MUST be able to see that they have to leave Trestle. Particular helpers are means, not the goal.

### Goal conflicts

| Conflict | Resolution |
|---|---|
| G1 vs G4 | Retain on disk within §9; gate what crosses into context. Never resolve by discarding evidence. |
| G1 vs fewer calls | Optimize total interaction cost (§18), not per-response bytes. |
| G2 vs G3 | Containment is automatic and silent; warn in the log, never in the agent's face. |
| G2 vs G4 | Capture limits bound recording; overflow truncates deterministically with counts preserved. |
| G3 vs immutability | Discovery tree is mutable; execution is from immutable snapshots (§6). |
| G3 vs G5 | The child runtime steers naive I/O into `work/` and auto-promotes `outputs/`; authors are not asked to learn filing. Unmanaged paths remain unmanaged (§2.5). |
| G3 vs foundation | Scripts extend capability; they MUST NOT become a second control plane. PluginSurface is the only crossing. |
| G9 vs G3 | TTY-class reach is an optional advertised capability, not a server-edit tax; M1–M7 remain complete without it. |
| G9 vs G4 | When the capability runs, completeness is recorded bytes plus counted gaps — never silent complete-looking loss. |
| G9 vs G8 | Requirement text names outcomes, not a vendor; deleting the means MUST NOT teach agents a new grammar. |

### The acid test

Every feature **MUST** answer yes to: *does this materially reduce agent context, or materially improve the reliability of arbitrary tool execution?* Anything failing this test belongs in §20 or nowhere.

---

## 2. Scope and constraints

v0.1 remains localhost POSIX work with managed evidence and unmanaged side effects. TTY-class oneshot is an optional advertised capability under that same aperture — not a new product, and not a silent hole.

### 2.1 Local-only

Loopback binding, same machine, same user privileges. **Plugin authorship is not a privilege boundary.** No review gates, signing, credential brokering, egress allowlists, authentication, or multi-tenancy.

### 2.2 POSIX-only

- **R-SCOPE-1** v0.1 **MUST** target POSIX platforms only. `fsync(dir)`, process groups, signals, fd inheritance, and process control have no portable Windows equivalent.

### 2.3 Non-goals

Not a security sandbox. Not a distributed scheduler. Not multi-tenant. Not a workflow engine. Not remote. Not an interactive TTY product in v0.1. Not agent-delegation-as-the-primary-job in v0.1.

**TTY-class oneshot is in scope as an optional capability** (§2.7): advertised, deletable, not a milestone-M1 organ. Interactive TTY control, live observation of a still-running run, and agent-delegation-as-primary are **deferred** (§20), not implied by oneshot.

### 2.4 Deferred to v0.2

Nested calls, pipelines, external callbacks, MRTR `input_required`. §20 states entry conditions. v0.1 plugins compose by direct Python import, losing telemetry linkage. Accepted.

TTY-class interactive control, live tail of a running run, and delegation-as-primary have **named entry preconditions** in §20; they are not "later, vaguely" implied by G9.

### 2.5 Managed data vs unmanaged side effects

**Managed** — recorded, bounded, filed under the run: everything through `Context`; process stdout/stderr and exit status; stdlib logging via the installed root handler; relative writes under the child's `work/` cwd; files auto-promoted from `work/outputs/`; stdlib subprocess descendants that stay in the run process group and inherit the captured fds (R-AUTO-1–5).

**Unmanaged** — possible, not prevented, not recorded: `open()` of paths outside `work/`; `os.environ` mutation; direct network; subprocesses that start a new session or process group; background threads outliving the call. `ctx.run_cmd` is the documented helper for bounded tails; it is **not** the only way ordinary subprocess console becomes managed.

- **R-SCOPE-2** Documentation and `describe_plugin` **MUST NOT** claim provenance over unmanaged side effects. TTY-class work that never entered the managed evidence path (everything not filed through `Context` and framework capture) remains unmanaged; v0.1 **MUST NOT** invent provenance theater for it.

### 2.6 Protocol environment

MCP `2026-07-28`: stateless core; Tasks as extension `io.modelcontextprotocol/tasks`; Roots/Sampling/Logging deprecated.

Three layers **MUST NOT** be conflated:

| Layer | Primitive | v0.1 status |
|---|---|---|
| **Trestle** | `await_runs(run_ids, …)` | Required |
| **MCP synchronous projection** | `tools/call` with `wait_ms` | Required. **M0: FastMCP 3.4.7 stdio `tools/call` works.** |
| **MCP asynchronous projection** | Tasks extension | **Not in v0.1.** FastMCP's implementation requires `fastmcp[tasks]` → **pydocket/Redis**, which **MUST NOT** be used (R-FMC-8). v0.1 wait correctness is `wait_ms` / `await_runs` on stdio (R-WAIT-4). A Trestle-owned mapper without Docket requires a later named amendment after a named client is measured unable to hold the call. |

Task creation, when implemented, is **server-directed** after the client advertises the extension.

- **R-SCOPE-3** Sampling **MUST NOT** be used.
- **R-SCOPE-4** State **MUST** travel as explicit handles, never hidden session state.
- **R-SCOPE-5** Protocol claims **MUST** be validated against the pinned FastMCP release (R-VER-9). **Partial:** stdio and in-process calls work on FastMCP 3.4.7; `ListToolsResult` has `nextCursor` and **no `ttlMs`**; FastMCP Tasks extra is Docket-backed and out of bounds.
- **R-SCOPE-6** Numeric figures are operational defaults unless marked as semantic invariants.

Trestle's v0.1 **agent** transport is **stdio MCP** (R-FMC-1). Notifications are an **optimization**, never a correctness mechanism (R-REG-2).

**Transport amendment (2026-08-25):** stdio MCP **MUST** remain the default agent transport for v0.1 — Cursor `.cursor/mcp.json` wiring and Phase C deliverables depend on it. A **separate** optional HTTP operator API for human inspection (R-OPS-10) does **not** amend R-FMC-1.

**Transport amendment E6 (2026-08-25):** streamable HTTP MCP **MAY** run **alongside** stdio via `trestle serve --transport streamable-http` — same ten tools, same admission, loopback bind only. Spec: [`hld/spec-agent-mcp-streamable-http.md`](hld/spec-agent-mcp-streamable-http.md). Does **not** replace stdio default; does **not** use FastMCP `http_app()` mount (R-FMC-4).

### 2.7 TTY-class local work (optional overlay)

Agents either complete TTY-class local work through the same run, wait, query, and fetch surface as any other run, or they can see that this install cannot do that work and that they must leave Trestle — never a slogan that ships neither. **TTY-class** means local programs whose observable behavior depends on a TTY, captured as **oneshot** evidence: start work, wait until the run reaches a terminal state, retrieve the same evidence class as any other run. v0.1 includes this as an **optional, advertised** capability. Ordinary commands stay on the existing pipe-capture path (R-CTX-2, §4.2). This overlay does not amend R-INV-1, R-MCP-1, request-versus-run, localhost POSIX, or spawn-per-run.

| ID | Observable behavior | Acceptance criterion | Priority |
|---|---|---|---|
| **R-REACH-1** | The system **MUST** either (a) let an agent complete TTY-class oneshot local work through the existing run / wait / query / fetch surface, with the same identity and evidence class as any other run, **or** (b) when that optional capability is not installed, present a named, agent-visible exclusion that TTY-class work is unavailable and that the agent **MUST** leave Trestle to perform it. Silence — neither an advertised capability nor a visible absence — **MUST NOT** satisfy this requirement. | An observer can point to either a published TTY-class capability that completes a named oneshot through the existing ten tools, or an explicit agent-visible unavailability with leave-Trestle guidance. An install with neither fails. | MUST |
| **R-TTY-1** | v0.1 **MAY** include a TTY-class oneshot capability. When present, that capability **MUST** be advertised through the existing plugin discovery surface. The ordinary POSIX local-work core (M1–M7) **MUST** remain complete when the capability is not installed. TTY-class work **MUST NOT** be required core machinery: adding or removing it **MUST NOT** be required for M1 exit. | With the capability unpublished, M1–M7 outcomes for ordinary POSIX local work still hold. When published, discovery lists it before `run` can admit it as TTY-class work. | MUST |
| **R-TTY-2** | When the capability is advertised and ready, TTY-class oneshot work **MUST** use the same handle grammar, the same named views, and the same fetch windows as any other run. Completeness is framework-observable bytes plus counted suppressions, retrievable after restart by the same query and fetch class. The agent-visible tool family **MUST NOT** grow (R-MCP-1). Agents **MUST NOT** be given a second identity system for this work. | After `evidence_finalized`, query and fetch of a TTY-class run return the same view names and handle prefixes as a pipe-captured run; no additional MCP tool is required; restart does not change retrievability of retained evidence. | MUST |
| **R-TTY-3** | TTY-class console evidence **MUST NOT** look complete while bytes were dropped, wrapped, or uncounted. Every gap **MUST** be a first-class countable fact. Absence of a gap record **MUST** mean no gap was observed, not that gaps were not checked. | Forcing a known loss during TTY-class capture yields a queryable incompleteness record (counts and `completeness` partial or equivalent). A projection that appears finished with uncounted loss fails. | MUST |
| **R-TTY-4** | If the TTY-class capability is missing or not ready at admission, the request **MUST** be refused: no `run_id`, no ledger row that pretends the work ran (R-RUN-1, R-MCP-4). If the request was admitted and the work then fails, the outcome **MUST** be a run in a terminal state (failed or the applicable existing class); evidence written before failure **MUST** be retained (R-EXEC-20). A run that "succeeds" with holes **MUST** remain classifiable as incomplete under existing completeness rules, not as silent success. | Missing or unreadiness at admit → request outcome, no `run_id`. Post-admit failure → terminal run with compact error. Incomplete evidence → completeness/gap records, not succeeded-with-hidden-loss. | MUST |
| **R-TTY-5** | When the TTY-class capability is not installed, agents **MUST** be able to observe that TTY-class work is unavailable on this install and that they **MUST** leave Trestle to perform it. "Optional" with neither an advertised capability nor a visible absence **MUST** fail R-REACH-1. | On an install without the capability, plugin discovery and/or describe/`run` refusal makes unavailability observable without implying a completed run. | MUST |
| **R-TTY-6** | When the capability is advertised but not ready to perform TTY-class work, that unreadiness **MUST** be observable before or at admission as a refusal, distinct from (a) plugin source validation failure (R-REG-5) and (b) a failed run. R-REG-5 **MUST NOT** be reinterpreted as helper unreadiness. The unreadiness channel **MUST** be the admission code `tty_not_ready` (no `run_id`). Catalog `capability_class` advertises the class; it **MUST NOT** carry helper health. | An advertised-but-not-ready capability produces `admission.tty_not_ready` with no `run_id`. `list_plugins(invalid=True)` / previous-version-kept remains the meaning of R-REG-5. Absence is no valid `tty-oneshot` row on a completed default-catalog walk. An agent can tell absence, unreadiness, and failed run apart. | MUST |
| **R-TTY-7** | Requirement sentences name outcomes. Any particular helper that implements TTY-class oneshot is optional means: deleting it **MUST NOT** require a new agent-visible grammar, a new MCP tool, or an amendment to R-INV-1 or R-MCP-1. Agents **MUST NOT** be taught a vendor session identifier. Untrusted helper bytes **MUST NOT** enter the MCP process except as bounded projection (R-INV-1). | Replacing or removing the means leaves ten tools, existing handle prefixes, and R-INV-1 true. Requirement text remains intelligible if that means never appears in the tree. | MUST |
| **R-TTY-8** | Ordinary commands that do not require a TTY **MUST** continue to use the existing pipe-capture execution path (R-CTX-2, §4.2). TTY-class execution **MUST NOT** become the default route for those commands. | A command that does not need a TTY still completes with pipe-captured stdout/stderr as specified in R-CTX-2; enabling the TTY-class capability does not reroute that command onto the TTY-class path by default. | MUST |
| **R-TTY-9** | Interactive stdin, TTY resize, and signal-as-agent-verbs; live observation of a still-running run; and agent-delegation-as-primary (including worktree isolation and nesting-environment stripping as primary product bets) **MUST NOT** be treated as satisfied by oneshot TTY-class capture. Each is deferred until its §20 entry condition is met. | A verification that only exercises oneshot start-wait-retrieve **MUST NOT** be claimed as coverage for interactive control, live tail of a running run, or delegation-as-primary. | MUST |

### 2.8 Script vs foundation

Trestle is two planes that MUST NOT be collapsed. The **foundation** wraps **scripts**. Scripts do not wrap, import, or drive the foundation.

| Plane | Owns | MUST NOT |
|---|---|---|
| **Foundation** | Admit, Execute, Project, ledger, registry, wrapper capture, child runtime (cwd, fds, logging handler, `result.index`, auto-promote), MCP porch | Run script code in the server or wrapper; pass writable `evidence/` to scripts; require scripts to know MCP or ledger layout |
| **Script** | One callable, its args/return, files it writes under `work/` | Import Kernel/FastMCP; append to the ledger; call MCP tools; write `evidence/`; depend on foundation module paths |

The wrap is **process + PluginSurface + environment**, not an author-facing telemetry SDK. Automatic capture (R-AUTO) is foundation behavior on the wrap.

- **R-BOUND-1** Foundation **MUST** wrap scripts. Script code **MUST NOT** wrap, subclass, or monkey-patch foundation processes or Kernel types. The child hosts both a foundation **runtime** (Context implementation, capture setup) and the **script**; those two MUST remain distinct modules. The runtime MAY import foundation internals; the script MUST NOT.
- **R-BOUND-2** From script source, the only permitted `trestle` imports are PluginSurface: decorator, `Context` protocol, `ArtifactRef`, and documented plugin-facing exceptions/codes. Validation **MUST** reject imports of Kernel, FastMCP, ledger, query, MCP adapter, wrapper, or run-directory layout helpers (R-PLUG-6, R-FMC-3).
- **R-BOUND-3** Crossing is one-way. Foundation observes script outputs (fds, files under `work/`, events, return value). Scripts **MUST NOT** call Admit, Execute, Project, or LedgerCommand, or construct MCP types.
- **R-BOUND-4** Script code **MUST** execute only in the disposable child (R-EXEC-1). The wrapper **MUST NOT** import the script snapshot (R-EXEC-6). The server **MUST NOT** import the script snapshot.
- **R-BOUND-5** Independent evolution: adding or removing a script **MUST NOT** require a foundation source edit (G3, R-MCP-1). A foundation internal change that does not bump PluginSurface **MUST NOT** require a script source edit.
- **R-BOUND-6** A script **MUST** be unit-testable with stub `Context` and no Kernel, FastMCP, or MCP server (R-PLUG-4, R-AUTO-6). Stub **MUST** honor PluginSurface plus the automatic environment semantics.

---

## 3. Domain model

Six objects: **PluginSnapshot**, **PluginVersion**, **RunSpec**, **Run**, **Artifact**, **Event**.

### 3.1 Run states

```
queued ──> running ──> succeeded
              │    └──> failed
              │    └──> cancelled
              │    └──> timed_out
              │    └──> worker_exit
              │    └──> crashed
              └──────> interrupted
```

- **R-RUN-1** `rejected` **MUST NOT** be a run state. Admission refusal is a request outcome with no `run_id`.
- **R-RUN-2** `run_id` **MUST** be `r_<base32-ms-timestamp><6 random base32>`. Uniqueness **MUST** be checked against existing run directories; collision **MUST** regenerate. Clock rollback **MUST NOT** be the sole uniqueness mechanism.
- **R-RUN-3** On startup, any run whose ledger lacks a terminal transition **MUST** be recovered per §5.6.
- **R-RUN-4** Terminal states **MUST** be immutable.
- **R-RUN-5** `interrupted` **MUST** be terminal and **MUST NOT** be automatically resumed in v0.1.
- **R-RUN-6** `worker_exit` and `crashed` **MUST** be distinct.
- **R-RUN-7** Internal run state **MUST NOT** be constrained to match protocol vocabulary.

### 3.2 The ledger

- **R-RUN-8** `ledger.ndjson` is authoritative. `meta.json` is derived and **MUST NOT** be consulted for recovery.
- **R-RUN-9** Happy path: `created → admitted → started → [progress | artifact_declared | artifact_available | limit_exceeded]* → execution_ended → evidence_finalized → <terminal>`. Recovery MAY append a documented suffix (§5.6).
- **R-RUN-10** The server **MUST** be the sole allocator of ledger sequence numbers.
- **R-RUN-11** Event sequence numbers are a separate worker-owned space.
- **R-RUN-12** Cross-stream ordering **MUST** be reconstructed from wall-clock timestamps only.

### 3.3 Ledger invariants

- **R-RUN-13** Sequence numbers **MUST** be unique, monotonic, and contiguous within a ledger.
- **R-RUN-14** Exactly one terminal transition.
- **R-RUN-15** `execution_ended` **MAY** occur at most once.
- **R-RUN-16** A terminal record **MUST** follow `evidence_finalized`. Recovery writes `evidence_finalized` with `completeness ∈ {complete, partial}` immediately before the terminal record when happy-path finalization did not occur.
- **R-RUN-17** After `evidence_finalized`, no evidence mutation is permitted.
- **R-RUN-18** Every `artifact_available` **MUST** correspond to exactly one prior `artifact_declared`. Every `artifact_declared` **MUST** resolve to `available` or `abandoned`.
- **R-RUN-19** The `created` record **MUST** include `spec_hash` (sha256 of `spec.json` bytes) and `service_epoch`.

---

## 4. Execution

### 4.1 Server → quiet wrapper → child (spawn-per-run)

**M0.5 decision.** Importing NumPy/pandas and performing a matmul starts native thread pools (4 OS threads on macOS, 16 on Linux) while Python-thread count stays 1. Python 3.12 then warns that `fork()` may deadlock. Fork of a warmed interpreter is **not** v0.1.

```
server (FastMCP, event loop, sockets, threads)
   │  spawn — never fork
   ▼
quiet wrapper (poll/select; no plugin imports; no native numeric stacks)
   │  spawn (fork+exec / multiprocessing spawn) — never fork-without-exec
   ▼
child (one per run, imports snapshot, disposable, never reused)
```

- **R-EXEC-1** Plugins **MUST** execute in a child process, never in the server or wrapper.
- **R-EXEC-2** Wrappers **MUST** be created with the `spawn` start method. Forking the FastMCP server is unsafe.
- **R-EXEC-3** A wrapper **MUST** be single-threaded in the strong sense: **no Python threads, no asyncio, no listening sockets**. It **MUST** multiplex pipes with nonblocking fds and `selectors`/`poll`. A poll reactor is required; "no event loop" means no asyncio.
- **R-EXEC-4** **Each run MUST execute in a freshly spawned child.** Children **MUST NOT** be reused. The child interpreter **MUST NOT** be created by `fork()` from a process that has imported plugin dependencies.
- **R-EXEC-5** Process reuse **MUST NOT** be relied upon for isolation.
- **R-EXEC-6** **v0.1 wrappers MUST NOT import the plugin snapshot or its third-party dependencies.** Warm-import supervisors are deferred until a snapshot's imports are proven OS-single-threaded (no native pools). Paying import cost per run is accepted (M0.5: ~0.6–0.7 s cached NumPy+pandas).
- **R-EXEC-7** Wrappers **MUST** be reaped after `supervisor_idle_s` (default 300) with no runs, or immediately when unused after snapshot supersession.
- **R-EXEC-34** **v0.1 wrapper topology:** one quiet wrapper process per **published snapshot**, not one wrapper per run and not one wrapper shared across snapshots. Children remain spawn-per-run (R-EXEC-4). Sharing a wrapper across snapshots would mix plugin import domains; one wrapper per run would multiply idle processes without isolation gain. Superseded wrappers are drained, not killed (R-REG-8).
- **R-EXEC-8** Each child **MUST** be its own process group leader.
- **R-EXEC-9** Descendant observation:

| Level | Mechanism | v0.1 |
|---|---|---|
| 1 | Process-group membership | **MUST** Linux and macOS |
| 2 | Tracked `ctx.run_cmd` pids | **MUST** Linux and macOS |
| 3 | Linux `PR_SET_CHILD_SUBREAPER` | **SHOULD** Linux; **MUST NOT** be assumed on macOS |
| 4 | True OS containment | §21; not a v0.1 promise |

"Known descendant" means levels 1–2 everywhere, plus level 3 on Linux when subreaper is set.

### 4.2 Capture pump

- **R-EXEC-10** Child fd 1 and fd 2 **MUST** be redirected to pipes drained by the wrapper's **single-threaded poll/select reactor**. **MUST NOT** be drained by a thread in the wrapper.
- **R-EXEC-11** The capture path **MUST** enforce per-stream byte limits, count suppressed bytes, and write bounded output to the run directory.
- **R-EXEC-12** fd-level interception **MUST** capture C extensions, grandchildren, and raw fd 1 writes.
- **R-EXEC-13** Evidence data **MUST NOT** pass through the server process.
- **R-EXEC-14** Pipes **MUST** be drained continuously; on limit exhaustion keep reading and discarding while counting.
- **R-EXEC-15** The pump **MUST** flush and close cleanly on child exit, including abnormal exit, before finalization. After `waitpid`, drain until EOF.

### 4.3 Cancellation and deadlines

- **R-EXEC-16** Flag → wait `grace_s` (10) → `SIGTERM` process group → wait `kill_s` (5) → `SIGKILL` group.
- **R-EXEC-17** `ctx.cancelled` **MUST** become `True` on the flag.
- **R-EXEC-18** Timeout follows the same path → `timed_out`.
- **R-EXEC-19** Terminate the process group and verify no known descendant remains (R-EXEC-9). Escapes → `orphaned_process` + wrapper recycle. Zero-descendant is a §21 seam.
- **R-EXEC-20** Evidence written before termination **MUST** be retained.
- **R-EXEC-21** Every run **MUST** carry an absolute deadline set at admission.

### 4.4 Failure classification

- **R-EXEC-22** Clean return → `succeeded`; uncaught exception → `failed`; `SystemExit`/nonzero clean exit → `worker_exit`; signal/OOM → `crashed`.
- **R-EXEC-23** A crashed child **MUST NOT**, by itself, crash the server, unpublish tools, or prevent admission of other in-limit runs. Not a host-health or noisy-neighbor guarantee (G2).
- **R-EXEC-24** Repeated crashes (default 3 in 60 s) for one snapshot **MUST** emit `supervisor_unstable` without halting the service.

### 4.5 Admission

- **R-EXEC-25** `max_active_runs` (default `min(8, cpu_count)`).
- **R-EXEC-26** Per-plugin concurrency limits independent.
- **R-EXEC-27** Queue depth bounded (256); excess → `queue_full` admission outcome.
- **R-EXEC-28** In v0.1 no run may block on another run.

### 4.6 Async plugin contract

- **R-EXEC-29** `def` and `async def` **MUST** be supported. The child owns the loop. The plugin **MUST NOT** call `asyncio.run()` inside an already-running loop.
- **R-EXEC-30** At successful return, no framework-created tasks MAY remain pending. Pending plugin tasks → `pending_tasks`.
- **R-EXEC-31** Blocking in `async def` waits until the deadline. Plugin threads are unmanaged except dying with the process group.

### 4.7 Service shutdown

- **R-EXEC-32** `SIGTERM`/`SIGINT` → `draining`: stop new runs (`service_draining`); reject queued-not-started as admission outcomes; allow running children until `shutdown_grace_s` (30); cancel remainder; finalize; fsync; terminate wrappers; exit.
- **R-EXEC-33** In-flight validation is abandoned (previous PluginVersion remains). Query ingestion stops at watermarks and **MUST NOT** block shutdown.

---

## 5. Storage, namespaces, and durability

### 5.1 Namespace separation

Plugin never receives a **writable** evidence path. Staging lives in `work/`.

```
$TRESTLE_HOME/
  service_epoch
  pins.json
  snapshots/<snapshot_id>/
  runs/<yyyy-mm>/<run_id>/
    evidence/                 FRAMEWORK-OWNED
      spec.json ledger.ndjson meta.json
      result.json result.index summary.json
      events.ndjson console/ artifacts/
    work/                     PLUGIN-VISIBLE
      artifact-staging/<opaque_id>.partial
```

- **R-STORE-1** Writable plugin namespace **MUST** be disjoint from evidence. `ctx.tmp` inside `work/`.
- **R-STORE-2** `ctx.artifact()` **MUST** return a path under `work/artifact-staging/`, never `evidence/`. Opaque id + `.partial`. Traversal rejected.
- **R-STORE-3** The framework **MUST NOT** pass any **writable** evidence path to plugin code. `ArtifactRef` resolution MAY expose the file read-only (R-PLUG-23).
- **R-STORE-4** `work/` removed at finalization unless non-success and `keep_work_on_failure` (default true).
- **R-STORE-22** Registration atomically promotes staging into `evidence/artifacts/<id>` (fsync → rename → fsync dir) and appends `artifact_available`.

### 5.2 Sources of truth

| Tier | Contents |
|---|---|
| **Authoritative** | snapshots, `spec.json`, `ledger.ndjson`, artifact bytes, `events.ndjson`, `result.json`, `result.index`, `pins.json`, `service_epoch` |
| **Materialized** | `meta.json`, `artifacts/_index.json`, query indexes |
| **Derived** | `summary.json`, views, doctor |

- **R-STORE-5** Materialized/derived **MUST** be reconstructible from authoritative sources.
- **R-STORE-6** One owner per file. Child: `events.ndjson`, `result.json`, `result.index`, `work/*`. Wrapper: `console/*`. Server: `spec.json`, ledger, `meta.json`, `summary.json`, `_index.json`, `pins.json`, `service_epoch`. After promotion, artifact bytes are immutable.
- **R-STORE-7** On child death, unfinished files transfer to the wrapper for finalization only.

### 5.3 Atomic writes

- **R-STORE-8** `spec.json`, `meta.json`, `result.json`, `result.index`, `summary.json`, `_index.json`, `pins.json`: tmp → fsync file → rename → fsync dir.
- **R-STORE-9** `ledger.ndjson` / `events.ndjson` append-only, complete JSON lines; ledger fsync every append; events at most every `event_fsync_ms` (250).
- **R-STORE-10** Readers tolerate a truncated trailing line (`trailing_partial_record`).
- **R-STORE-11** Artifacts written `.partial` under staging, renamed at registration.
- **R-STORE-23** Event finalization applies R-STORE-10, then fsync.
- **R-STORE-24** Admit: create month dir + fsync parent; create run dir + fsync month; create evidence/work + fsync run dir; write `spec.json`; append `created`; fsync ledger and run dir. Crash before durable `created` → sweep.

### 5.4 Result presence

- **R-STORE-12** `result.json` exists only when the plugin returned a value. Never synthesize for failures.
- **R-STORE-13** `result_state ∈ {absent, complete, invalid, too_large}`.

### 5.5 Finalization

- **R-STORE-14** `evidence_finalized` requires durable result or recorded absence; `result.index` durable or recorded absence; events flushed; pump closed; artifacts resolved; limits marked; `work/` handled. Record `completeness ∈ {complete, partial}`.
- **R-STORE-15** Agent **MUST NOT** see terminal before `evidence_finalized` is durable.
- **R-STORE-16** Finalization failure → `failed`/`finalization_failed`, never `succeeded`. Recovery still writes `evidence_finalized` (`partial`) then `interrupted`.
- **R-STORE-17** Bounded by `finalize_timeout_s` (30).

### 5.6 Crash recovery algorithm

- **R-STORE-18** On startup: new `service_epoch`; for each run dir read ledger (never meta):

| Last durable record | Action |
|---|---|
| no ledger | Sweep |
| `created` / `admitted` / `started` / mid-execution | Recovery suffix → `interrupted` |
| `execution_ended` | Suffix → `interrupted`; `result_state` complete iff `result.json` parses |
| `evidence_finalized` | Promote recorded terminal, or append `interrupted` |
| terminal present | Rematerialize `meta.json`. **Do not append.** |

**Recovery suffix:** resolve artifacts; sweep tmp/partial; freeze evidence; append `evidence_finalized` (`partial` unless completeness proven); append exactly one `interrupted`; fsync.

- **R-STORE-19** Idempotent: second pass sees terminal and appends nothing. Tested.
- **R-STORE-20** Recovery **MUST NOT** require the query backend.
- **R-STORE-21** Every table row is an automated test.
- **R-STORE-25** Workers whose `service_epoch` ≠ current are orphans: reap; `stale_worker` if unconfirmed; never adopt.

---

## 6. Identity and provenance

- **R-ID-1** Registration copies source into `snapshots/<snapshot_id>/` from `source_sha256`. Immutable.
- **R-ID-2** PluginVersion executes from immutable bytes, never `plugins/foo.py`.
- **R-ID-3** Workers import from the snapshot.
- **R-ID-4** RunSpec records `snapshot_id`; that snapshot runs regardless of later edits.
- **R-ID-5** Retain snapshots referenced by retained runs.
- **R-ID-6** Snapshot store is authoritative registry state.
- **R-ID-7** `discovery_path` is mutable metadata, not identity.
- **R-ID-13** Record `source_sha256`, `schema_sha256`, `manifest_sha256`. Identity remains `source_sha256`.
- **R-ID-8** Source and interface provenance only.
- **R-ID-14** **A run identity is not a reproducibility identity.**
- **R-ID-9** Every run records plugin, version, hashes, snapshot, args_hash, Python version, platform.
- **R-ID-10** `args_hash` is sha256 of canonical serialization.
- **R-ID-11** Re-registering `(name, version)` with new source is permitted → new snapshot, `version_source_changed`.
- **R-ID-12** `run` optional `version`; omitted = latest, resolved identity recorded.

| Type | Meaning | v0.1 |
|---|---|---|
| `Path` | Unmanaged host path | Supported |
| `ArtifactRef` | Managed immutable artifact | Supported |
| `InputFile` | Hashed external input | Deferred |

---

## 7. Plugin contract

- **R-PLUG-1** Schema from type hints.
- **R-PLUG-2** Description = first docstring line.
- **R-PLUG-3** Required semver `version`.
- **R-PLUG-4** Testable with stub `Context`.
- **R-PLUG-5** `def` and `async def`.
- **R-PLUG-6** No service internals. The supported import surface for scripts is PluginSurface only (R-BOUND-2). Importing `trestle.server`, `trestle.wrapper`, Kernel ports, FastMCP, or evidence paths **MUST** fail validation.
- **R-PLUG-7** Supported params: `str` `int` `float` `bool` `None` `Path` `datetime` `date` Enum `Literal` `list[T]` `dict[str,T]` `set[T]` `Optional` `Annotated` dataclasses/Pydantic (depth default 5) `ArtifactRef`.
- **R-PLUG-8** Reject `bytes`, heterogeneous tuples, non-str dict keys, untagged unions >2 non-None, `Any`, unparameterized generics, callables, arbitrary classes, recursive types.
- **R-PLUG-9** Return types from the same subset.
- **R-PLUG-10** Subset documented; errors point to it.
- **R-PLUG-22** One canonical path: annotation → type AST → JSON Schema → schema hash. No separate dataclass vs Pydantic downstream.
- **R-PLUG-11** `ArtifactRef` distinct type.
- **R-PLUG-12** Resolve before invoke or `artifact_not_found` / `expired` / `missing`.
- **R-PLUG-13** Resolution recorded in RunSpec (reachability).
- **R-PLUG-14** `Path` MUST NOT accept artifact handles.
- **R-PLUG-23** Resolved artifacts are **read-only**. Writable copy via `ctx.copy_artifact` into `ctx.tmp`.
- **R-PLUG-24** Artifact bytes immutable after `artifact_available`.
- **R-PLUG-15** `effects` are hints, never enforced.
- **R-PLUG-16** Validate in a throwaway subprocess.
- **R-PLUG-17** Clean import, decorator, type subset, `summary_budget` ≥ minimum.
- **R-PLUG-18** Snapshot only after validation.
- **R-PLUG-19** Failure leaves previous version serving.
- **R-PLUG-20** `import_failed` with best-effort diagnosis.
- **R-PLUG-21** No auto-install of dependencies.

Decorator defaults: `timeout_s=300`, `summary_budget` = minimum×2, `idempotent=False`, `promote=False`.

---

## 8. Context API

```python
ctx.artifact(...) -> Path          # staging in work/
ctx.attach(path, ...) -> str       # handle; promotes into evidence
ctx.copy_artifact(handle) -> Path  # writable copy in ctx.tmp
ctx.retain(handle)
ctx.run_cmd(...) -> CmdResult
ctx.log / ctx.progress / ctx.tmp / ctx.outputs / ctx.cancelled / ctx.deadline
```

- **R-CTX-1** `ctx.log` writes events.ndjson directly. Framework also installs a root logging handler before invoke.
- **R-CTX-5** Do not trust logging config. Handler removal → `logging_hijacked`. `ctx.log` still works. Not a run failure.
- **R-CTX-2** `ctx.run_cmd` captures via §4.2; returns tails capped at 2 KB for plugin logic.
- **R-CTX-3** `run_cmd` children in the run process group.
- **R-CTX-4** All Context output subject to §9.
- **R-CTX-6** `copy_artifact` MUST NOT return an evidence path.
- **R-CTX-7** `ctx.tmp` is `work/tmp`. `ctx.outputs` is `work/outputs` (the keeper tree; R-AUTO-3).

### 8.1 Automatic child runtime (script-writer path)

The PluginSurface and child process environment **MUST** make the managed path the default for ordinary Python scripts. An author who writes one plugin file using the Python standard library plus Context public members **MUST NOT** need to know ledger layout, MCP tools, handle grammar, projection budgets, or FastMCP in order for framework-observable outputs to be filed, bounded, and projected correctly (G3, G5, G8, R-BOUND-1). Correctness is enforced by the **foundation** child runtime wrapping the script, not by author discipline.

- **R-AUTO-1** The child working directory **MUST** be the run's `work/` root. Relative `open()` / `Path` writes land under `work/`. The snapshot directory **MUST NOT** be the cwd and **MUST NOT** be writable by the plugin (execution is from immutable snapshot bytes; R-ID-2).
- **R-AUTO-2** `TMPDIR` (and equivalent tempfile roots the child controls) **MUST** point inside `work/tmp`. Naive `tempfile` use **MUST NOT** land on the host temp directory.
- **R-AUTO-3** At child start the runtime **MUST** create `work/outputs/` and `work/tmp/`. Regular files remaining under `work/outputs/` at `execution_ended` **MUST** be auto-declared and promoted as artifacts on the same atomic path as `ctx.attach` (R-STORE-22, R-ART-1). Authors MAY still call `ctx.artifact` / `ctx.attach` explicitly. Scratch under `work/tmp` and undeclared files elsewhere under `work/` are **not** auto-promoted and are removed with `work/` on successful finalization (R-STORE-4).
- **R-AUTO-4** Framework-observable streams **MUST** be captured without author action: fd 1/2 (R-EXEC-10), stdlib `logging` via the installed root handler (R-CTX-1), return-value streaming serialization and `result.index` (R-LIM-8, R-BUD-22). Print loops and huge returns are bounded by §9–§10 automatically.
- **R-AUTO-5** Stdlib `subprocess` / `os.system` / `asyncio` subprocess helpers that do not start a new session **MUST** remain in the run process group and **MUST** inherit the captured fds unless the author redirects them. `ctx.run_cmd` remains the documented helper (2 KB tails for plugin logic). It is **not** required for ordinary subprocess stdout/stderr to hit the wrapper pump. `start_new_session=True` / an explicit new process group is unmanaged (R-EXEC-19).
- **R-AUTO-6** Plugin code **MUST NOT** need to import FastMCP or Kernel types, or write evidence paths, to be correct (R-PLUG-6, R-FMC-3). Stub Context used in pytest **MUST** apply the same cwd / `TMPDIR` / `outputs/` auto-promote / logging-handler semantics so tests that print, log, and write `outputs/` match production filing (R-PLUG-4).
- **R-AUTO-7** Automatic **MUST NOT** be read as a host sandbox. Absolute `open()` outside `work/`, network, env mutation, and session-escaping subprocesses remain unmanaged (§2.5, R-LIM-14). Documentation **MUST NOT** claim provenance over them (R-SCOPE-2). Auto-promote **MUST** obey artifact byte/count limits (R-LIM-1); excess → limit markers, not silent drop without counts (R-LIM-3).

---

## 9. Resource limits

Figures are defaults except R-LIM-3, R-LIM-4, R-LIM-6, R-LIM-9, R-LIM-10 (invariants).

- **R-LIM-1** Defaults: result 256 MB; 100k events; 64 MB event bytes; 5k events/s; 64 MB/console stream; 64 MB child log; 1000 artifacts; 2 GB artifact bytes; 64 KB single event.
- **R-LIM-2** Overridable per plugin.
- **R-LIM-3** Markers record stream, limit, bytes recorded, **bytes suppressed**.
- **R-LIM-4** Capture exhaustion does not fail the run.
- **R-LIM-5** `limits_exceeded` in meta, ledger, status frame.
- **R-LIM-6** Token-bucket; drop-with-counting; never block.
- **R-LIM-15** Capacity = rate (1 s burst); refill = rate/s; ms precision. Dropped events count as suppressed, not retained.
- **R-LIM-16** Order: size → rate → count → bytes. One marker/class/s; markers exempt from the rate bucket.
- **R-LIM-7** Console: first 25% + last 75% + elision marker.
- **R-LIM-8** Children stream serialization to `result.json`.
- **R-LIM-9** Over `max_result_bytes` → `result_too_large`.
- **R-LIM-10** Server **MUST NOT** load `result.json` in full. Uses `result.index`.
- **R-LIM-11** Building a huge object may OOM the child (`crashed`). `max_result_bytes` is not a memory bound.
- **R-LIM-12** Sample `work/` every `work_check_interval_s` (10).
- **R-LIM-13** Over `max_work_bytes` (10 GB) → `work_limit_exceeded` + cancel.
- **R-LIM-14** Writes outside work/evidence are unmanaged.
- **R-LIM-17** Console and events are **byte streams**.
- **R-LIM-18** Text fetch decodes UTF-8; invalid sequences → U+FFFD + `invalid_utf8`. Lines are newline-delimited byte records.

---

## 10. Return budget and projection

- **R-BUD-1** Canonical: UTF-8, no ASCII escaping, sorted keys, `(",",":")`, shortest round-trip floats.
- **R-BUD-2** NaN/Infinity → `non_canonical_value`.
- **R-BUD-3** Byte counts on UTF-8 of that form.
- **R-BUD-4** Full value to `result.json` when present.
- **R-BUD-5** If it fits `summary_budget`, return verbatim.
- **R-BUD-6** Else: object field-skip; array `{count, sample, handle}`; scalar handle only (never truncate strings); `None` always fits; nested objects top-level only.
- **R-BUD-7** Add whole fields in `summary_fields` order; never partial fields.
- **R-BUD-8** Footer: handle, `result_bytes`, `truncated`, `omitted`.
- **R-BUD-9** Deterministic. Tested.
- **R-BUD-10** `summary_fields` on non-object rejected at registration.
- **R-BUD-11** Server middleware enforces the budget. Child may write `result.index`; it cannot raise the cap.
- **R-BUD-12** No universal envelope.
- **R-BUD-13–17** Status frame as in v0.3 (run_id, state, duration_ms, counts; errors compact; no tracebacks).
- **R-BUD-18–20** Computed `minimum_envelope_bytes`; reject smaller budgets; version as `status_frame_version`.
- **R-BUD-21** `summary.json` records exact bytes + hashes + algorithm versions.
- **R-BUD-22** Child writes `result.index` during streaming serialization: `root_type`, `byte_length`, field byte ranges, array count/sample ranges.
- **R-BUD-23** Index bounded (`max_index_bytes` 64 KB). Else coarsen + `index_truncated`. **M0.6: 20k-key object and 8e6-element array both stayed ≤64 KB.**
- **R-BUD-24** Server builds summary from index + `pread` ranges. Never slurp. Child death mid-write → `result_state=invalid`. **M0.6 passed** (`spikes/RESULTS.md`).

---

## 11. Artifacts

Lifecycle: `declared → writing → available → collected`, or `abandoned` / `missing`.

- **R-ART-1** `ctx.artifact()` records `declared` immediately.
- **R-ART-2** Finalization resolves every declared artifact to available or abandoned.
- **R-ART-3** Abandoned retained, fetchable, not swept as orphans. **Product default:** `fetch` of an abandoned artifact **MUST** succeed until retention expiry or collection (same fetch windows as available artifacts). Abandoned is a retention class, not a hide-from-fetch flag.
- **R-ART-4** Files under artifacts/ with no declared record are orphans → sweep.
- **R-ART-5** Abandoned default retention 24 h.
- **R-ART-6** Handles `art_<base32>`, not run-scoped.
- **R-ART-7** Record fields as in v0.3.
- **R-ART-8** `retention_class ∈ {default, short, pinned}`.
- **R-ART-9** Reachability, not refcounting.
- **R-ART-10** References: ArtifactRef resolution, `ctx.retain`, pin — in spec/ledger or `pins.json`.
- **R-ART-11** Queries/CLI/content mentions are not references.
- **R-ART-12** Reachable if a referencing run is retained or pinned.
- **R-ART-13** Collection keeps the record (`artifact_expired`).
- **R-ART-14** `pin`/`unpin` on CLI and MCP. State in `$TRESTLE_HOME/pins.json`, not a query backend.
- **R-ART-15** Pins survive producer-run deletion. Abandoned MAY be pinned. Collected MUST NOT; no resurrection.
- **R-ART-16** `unpin` of unpinned is success no-op.
- **R-ART-17** Files auto-promoted from `work/outputs/` (R-AUTO-3) **MUST** appear in `run_artifacts` with the same handle grammar as `ctx.attach`. Auto-promote **MUST NOT** invent a second identity system.

---

## 12. Query layer

- **R-QB-1** Named view set is the portable contract.
- **R-QB-2** Identical semantic payloads modulo `backend` and `as_of`. Specified ordering MUST match.
- **R-QB-3** Raw query languages are not portable.
- **R-QB-26** Ordering: `recent_runs`/`recent_failures` newest-first (`run_id` desc); `run_events` oldest-first; `run_tail` oldest-first within tail; `run_artifacts` declaration order; `artifact_refs` producer first then `run_id` asc; `last_error` events oldest-first.
- **R-QB-27** Envelope `{items, next_cursor, truncated, backend, as_of}`. Opaque cursors; stale → `cursor_expired`. `backend` and `as_of` are required (R-QB-5–9); they **MUST NOT** change row semantics.

v0.1 views: `run`, `last_error`, `run_tail`, `run_events`, `recent_runs`, `recent_failures`, `run_provenance`, `run_artifacts`, `artifact_refs`. **`plugin_stats` is not a v0.1 view.** The v0.1 set **MUST** be treated as sufficient for M1–M7 and for TTY-class oneshot (completeness is `RunView.limits_exceeded` plus `fetch` of attached handles, not a tenth view). A missing view **MUST** be a named requirements amendment, not a silent extra MCP tool or ViewName.

- **R-QB-28** Until durable `evidence_finalized` for the addressed run, MCP `query` **MUST** only serve `run` (identity/status row) and `recent_runs` (may include non-terminal rows). `last_error`, `run_tail`, `run_events`, `run_provenance`, `run_artifacts`, `artifact_refs`, and `recent_failures` **MUST** return `projection.not_finalized` (`retryable=true`). Live tail of a still-running run is **not** v0.1 (R-TTY-9, §20). Agents waiting on work **MUST** use `wait_ms` / `await_runs`, not query-polling of console/events.

- **R-QB-4** Partial backend implementation fails startup.
- **R-QB-5–9** Entry-point backends; derived caches; `as_of`+`backend` on results; backend failure does not fail runs (filesystem fallback); not on the write path.
- **R-QB-10–14** Filesystem backend ships, is default and reference oracle, recency cache 500, `as_of=now` for a single file, **MUST NOT** claim cross-file atomicity stronger than the ledger. Unbounded scans → `backend_scan_limit`. **`plugin_stats` is not a v0.1 view and MUST NOT be added as a volume valve.**
- **R-QB-15** SQLite is specified and MUST pass conformance before advertising. **MAY** follow filesystem (M5.1). Not a gate for M1–M4 or G1.
- **R-QB-16–20** Single serialized writer; WAL; batch ingest; live rebuild; `raw` CLI-capable.
- **R-QB-21** MCP `query` is view+params only.
- **R-QB-22** `trestle query --sql` CLI on capable backends, bounded.
- **R-QB-23** Every collection API bounds item count **and** bytes. Semantic invariant.
- **R-QB-24** Truncation signaled, never silent.
- **R-QB-25** Per-cell truncation default 512 bytes.

---

## 13. MCP surface

Tools: `list_plugins`, `describe_plugin`, `publish_plugin`, `run`, `await_runs`, `cancel`, `query`, `fetch`, `pin`, `unpin`.

- **R-MCP-1** Core count MUST NOT grow with plugin count.
- **R-MCP-2** `list_plugins` MUST NOT return schemas.
- **R-MCP-3** Descriptions state side effects; do not restate schema types.
- **R-MCP-4** Request outcomes (`plugin_not_found`, `invalid_args`, `queue_full`, `idempotency_key_conflict`, `service_draining`, `tty_not_ready`, …) have no `run_id`.
- **R-MCP-5–8** Promotion byte budget 8192; refuse when exhausted; identical telemetry; CI benchmark.
- **R-MCP-9** View catalog as MCP resources.
- **R-MCP-10** RunState authoritative; Task status is a projection.
- **R-MCP-13** Keep `await_runs`, `wait_ms`, and Tasks as three layers. **v0.1 implements only the first two.** The Tasks layer is unimplemented (R-WAIT-4).
- **R-MCP-11** Mapping as v0.3 (plugin exception → task `completed` + `isError`; crash/interrupt → task `failed`), subject to a Trestle-owned mapper if Tasks are implemented.
- **R-MCP-12** Plugin exceptions MUST NOT map to task `failed`.

---

## 14. Waiting

- **R-WAIT-1–8** `wait_ms` default 2000; else `{run_id, state:running}`; `await_runs` modes all/any/first_failure; progress interval 15 s subject to client; timeout returns partial; status frames only; joins MUST NOT occupy a worker.
- **R-WAIT-14** `ControlSurface.run` **MUST** admit, then start `Conductor.drive` on a **background worker thread** for new runs — **MUST NOT** block the caller on full plugin duration before honoring `wait_ms`. Composition: admit → background `drive()` → `Project.status` when `wait_ms` is 0, else `Project.await_one(run_id, wait_ms)`. Idempotent replay (`existing=True`) **MUST NOT** start a second drive. Joins (`await_runs`, `await_one`) **MUST NOT** occupy a wrapper worker (R-WAIT-1).
- **R-WAIT-9** `await_runs` is a Trestle abstraction.
- **R-WAIT-4** **v0.1 MUST NOT implement an MCP Tasks mapper.** FastMCP `task=True` / Docket is forbidden (R-FMC-8). Wait correctness is `wait_ms` on `run` and `await_runs` on stdio `tools/call`. M0 showed FastMCP 3.4.7 stdio `tools/call` works. A Trestle-owned mapper without Docket **MAY** be added only by a later named amendment after a **named** target client is measured unable to hold a multi-minute stdio call.
- **R-WAIT-10–12** Idempotency key + TTL 3600; match plugin+snapshot+args_hash; conflict if reused with different values; args-hash dedup opt-in.
- **R-WAIT-13** Keys reconstructible from spec/ledger across restart.

---

## 15. Fetch

- **R-FET-1–7** Bounded range/head/tail/grep/jsonpath; default last 50 lines; report fraction; grep caps matches; binary metadata only; `<run_id>/result` pseudo-artifact; distinguish expired/missing/abandoned/not_found.
- **R-FET-8** **Fetch never accepts filesystem paths.** Path-shaped strings → `invalid_handle`.
- **R-FET-9** `max_scan_bytes` 64 MB and `max_scan_time_ms` 2000; overflow → partial + `scan_budget_exceeded`.

---

## 16. Registry and hot reload

- **R-REG-1** Watch `plugins/` debounced 250 ms.
- **R-REG-2** Validate → snapshot → publish → increment `registry_version`. `tools/list_changed` is an optimization. Correctness: a client that refreshes `tools/list` eventually sees the new publication.
- **R-REG-3** Atomic pointer swap to an immutable version record.
- **R-REG-4** Run captures `snapshot_id` at creation.
- **R-REG-5** Validation failure leaves previous version; `list_plugins(invalid=True)`.
- **R-REG-6** `tools/list` carries `registry_version`. **`registry_version` is the publication fact for v0.1 even if a later FastMCP pin grows `ttlMs`.** `ttlMs`, if present, is an optimization hint only — never correctness, never a second freshness channel (R-REG-2). FastMCP 3.4.7 `ListToolsResult` has no `ttlMs`.
- **R-REG-7** Write-to-callable < 2 s when deps are present. Boundary: **filesystem change observed → successful `run()` admission.**
- **R-REG-8** Superseded wrappers drained, not killed.

---

## 17. Errors and operations

- **R-ERR-1–6** Origin classes plugin/framework/execution/storage/admission; stable `code`; message <200 chars; `retryable` only when it changes recovery; tracebacks in events only; plugin codes namespaced.
- **R-OPS-1–4** Retention defaults 180d metadata / 7d artifacts / 24h abandoned / 10 GB cap; GC respects reachability and pins; retain referenced snapshots; sweep orphans/tmp.
- **R-OPS-5–9** `config.toml` with working defaults; `service.log` not stdout; `doctor` includes epoch and drain; CLI includes `pin`/`unpin`/`recover`; CLI verbosity exempt from §10.
- **R-OPS-10** Optional **HTTP operator API** for human inspection (Phase D). **MAY** expose read-only endpoints mirroring ControlSurface `query` / `fetch` admission rules — not a ninth MCP tool, not live tail, not `run` from UI unless a future spec explicitly requires it. **MUST** be a Trestle-owned HTTP app (FastAPI or Starlette), **not** FastMCP `http_app()` (R-FMC-4). **MUST NOT** replace stdio MCP as the agent transport (R-FMC-1). Same verb **MUST NOT** have different admission rules across MCP, CLI, and operator HTTP (HLD grandparent constraint).

---

## 18. Cost model

- **R-COST-1** `expected_context_cost = tokens_consumed + λ × model_turns` where **λ is the equivalent token cost of one additional model decision under the benchmark workload**.
- **R-COST-2** Bytes are the control variable; tokens measured; turns counted.
- **R-COST-3** λ calibrated from §19.3 at **M7**. Until that calibration, G1 still binds via DefaultAgentSuccess and byte/turn discipline; design **MUST NOT** wait on a numeric λ to freeze envelopes.
- **R-COST-4** Named pinned tokenizer.
- **R-COST-5** Boundary: canonical payload to the MCP client, excluding transport framing.
- **R-COST-6** Record input/output/tool-call tokens, turns, wall time, **task success rate**. Headline: **context cost per successfully completed task**.

---

## 19. Verification

- **R-VER-1** Scenarios pass only with a correctness oracle **and** the byte budget (table as v0.3 plus oracles: expected value / diagnosis+retry / fetch a specified field / first-finisher identity).
- **R-VER-2** Hostile plugin as v0.3; assert control-plane responsiveness, not host immunity.
- **R-VER-3** Chaos matrix as v0.3 plus: recovery twice; torn events; consumer write through ArtifactRef; idempotency after restart; SIGTERM drain; fetch of a filesystem path.
- **R-VER-4–5** Workflow benchmarks include success rate; A must beat baseline on cost per successful task.
- **R-VER-6–8** Serialization determinism; all shapes; tool-definition bytes.
- **R-VER-9** Protocol spike. **Executed 2026-08-19 against FastMCP 3.4.7:** stdio works; no `ttlMs` on list; FastMCP Tasks extra is Docket/Redis — out of bounds. Remaining: target-client wait duration and whether a Trestle-owned Tasks mapper is required.
- **R-VER-10** Backend conformance, semantic payload, exclude `backend`/`as_of`.
- **R-VER-11** Process spike. **Executed on macOS and Linux.** Result: **spawn-per-run** (R-EXEC-4, R-EXEC-6).
- **R-VER-12** Projection spike. **Passed** (`spikes/out/m06/report.json`).

---

## 20. Deferred features

Unchanged: nested calls, linear pipelines, callbacks, MRTR re-invocation (not continuation). **`InputFile`** hashed external input deferred. **Warm-import supervisors** deferred until import is OS-single-threaded.

**Interactive TTY control** (agent-driven stdin, resize, signal-as-agent-verbs). Deferred. **Entry condition:** a named requirements amendment is accepted that agents must drive a live TTY, not merely retrieve oneshot evidence. Passing oneshot capture does not open this gate. Until then, stop remains the existing cancel path.

**Live observation of a still-running run** (named views such as `run_tail` / `run_events` while the run is not yet in a terminal state). Deferred. **v0.1 answer is no** (R-QB-28). **Entry condition:** a later named amendment records a yes. Oneshot TTY-class success waits until the run is in a terminal state; it does not imply live tail.

**Agent-delegation-as-primary** (worktrees, nesting-environment stripping, or "Trestle is how you delegate agents" as the product thesis). Deferred. **Entry condition:** each complementary item independently answers yes to the acid test as its own keep. Harvested invocation notes may live in deferred notes; they **MUST NOT** become v0.1 obligations by riding TTY-class oneshot.

---

## 21. Isolation seams

Unchanged: rlimits/cgroups, namespaces, credentials, egress, signed snapshots, multi-user. **R-ISO-1** single identifiable call sites.

---

## 22. Changes from v0.6

**Script / foundation seam.** Two planes are now vocabulary and MUST text (§2.8, R-BOUND-1–6). Foundation wraps scripts. PluginSurface is the only script-facing contract. Automatic filing remains foundation behavior on that wrap (R-AUTO), not a script SDK.

v0.6 closed §25 and added R-AUTO. v0.5 TTY overlay remains in force.

### Change log (this revision)

- Name two planes: foundation vs script → vocabulary **foundation**, **script / plugin**, **PluginSurface**.
- Crossing rules → **R-BOUND-1–6**, **§2.8**; tighten **R-PLUG-6**.
- Goal conflict G3 vs foundation: scripts must not become a second control plane.

---

## 23. Milestones

**M0 — Protocol spike.** R-VER-9. **Done (partial):** stdio/in-process FastMCP 3.4.7. Tasks extra rejected. **v0.1 wait is stdio `wait_ms` / `await_runs` (R-WAIT-4); no Tasks mapper.**

**M0.5 — Process architecture.** R-VER-11. **Done.** Spawn-per-run selected. Wrapper topology: one quiet wrapper per published snapshot (R-EXEC-34).

**M0.6 — Projection.** R-VER-12. **Done.**

**M1 — Skeleton.** FastMCP stdio; snapshots; spawn-per-run quiet wrapper + child; script/foundation seam (PluginSurface only in script); automatic child runtime (cwd=`work/`, `TMPDIR`, `outputs/`); staging namespaces; poll capture; ledger; status frame; epoch; drain.

**M2 — Bounding.** Projection via `result.index`; capture limits; artifacts + pins; fetch (abandoned fetchable by default); auto-promote `work/outputs/`. *Exit: R-VER-2.*

**M3 — Durability.** Cancel/timeout; atomic writes; recovery. *Exit: §5.6 + second pass.*

**M4 — Waiting.** `await_runs` / `wait_ms` on stdio. **No** Trestle Tasks mapper in v0.1.

**M5 — Query.** Filesystem backend, views, paging; `backend`/`as_of` on envelopes; R-QB-28 running-run restriction. Volume: `backend_scan_limit`, no `plugin_stats`.

**M5.1 — SQLite.** Optional for filesystem-complete v0.1.

**M6 — Extending.** Hot reload, `registry_version`, promotion budget. An optional TTY-class capability, when shipped, publishes like any other plugin; it is **not** an M1–M7 gate.

**M7 — Operations.** Retention, CLI, doctor, chaos + workflow benchmarks.

**v0.2 — Composition.** §20.

---

## 24. FastMCP

- **R-FMC-1** Standalone stdio in the server process only.
- **R-FMC-2** Trestle owns execution. **FastMCP's task executor MUST NOT become Trestle's executor.** Confirmed: `fastmcp[tasks]` → pydocket; handlers use Redis/Docket.
- **R-FMC-3** Wrappers and children MUST NOT import FastMCP.
- **R-FMC-4** MUST NOT mount MCP via FastMCP `http_app()`. A separate Trestle-owned operator HTTP app (R-OPS-10) is **not** an `http_app()` mount and does **not** amend this rule.
- **R-FMC-5** Child `Context` wraps, does not subclass, FastMCP's.
- **R-FMC-6** Pin FastMCP; re-run R-VER-9 on bump. Current spike pin: **3.4.7**.
- **R-FMC-7** Starlette ≥ 1.0.1 (CVE-2026-48710).
- **R-FMC-8** Distributed task backends (Docket, Redis) **MUST NOT** be enabled.

```
trestle/
  server/     admission scheduler ledger recovery registry snapshots
              artifacts gc shutdown pins
              # tasks.py mapper is not v0.1 (R-WAIT-4)
  query/      backend.py fs.py sqlite.py views.py conformance.py
  wrapper/    main.py reactor.py spawn.py
  child/      main.py context.py serialize.py index.py
  common/     canonical.py codes.py spec.py types.py schema_ast.py
```

---

## 25. Closed decisions (former open questions)

Answers are binding for v0.1. Reopening any item is a named amendment, not a silent design choice.

1. **Do target MCP clients hold a multi-minute stdio `tools/call`?** **v0.1 stdio MCP is retained for agents** (owner 2026-08-25). M0 showed FastMCP 3.4.7 stdio `tools/call` works. Wait correctness is non-blocking `wait_ms` / `await_runs` on stdio (G6, G1, R-WAIT-14) — agents **MUST NOT** block for full plugin duration when `wait_ms` times out. **Do not ship a Tasks mapper in v0.1** (R-WAIT-4). A named client measured unable to hold any stdio call may justify streamable HTTP MCP or a Trestle-owned Tasks mapper without Docket — never FastMCP Docket (R-FMC-8).

2. **Will a later FastMCP pin grow `ttlMs` on `tools/list`?** Irrelevant to correctness. **`registry_version` is the publication fact** even if `ttlMs` appears (R-REG-6). `ttlMs` is an optimization hint only.

3. **Supervisor proliferation?** **One quiet wrapper per published snapshot** (R-EXEC-34). Idle reap `supervisor_idle_s` 300; supersession drain not kill (R-EXEC-7, R-REG-8). Not one wrapper per run; not one wrapper shared across snapshots.

4. **Filesystem backend at volume without `plugin_stats`?** **`plugin_stats` stays out of v0.1.** Protection is `backend_scan_limit` plus recency cache 500 (R-QB-10–14). Operators use CLI `--sql` / `doctor`, not a new MCP view (G1, G7).

5. **Is the v0.1 view set sufficient?** **Yes.** Nine named views. TTY-class completeness is `RunView.limits_exceeded` plus `fetch(art_…)`, not a tenth view. A missing view is a named amendment.

6. **Abandoned artifacts fetchable by default?** **Yes.** `fetch` succeeds until retention expiry or collection (R-ART-3). Abandoned is a retention class, not a hide flag. Distinguish `abandoned` / `expired` / `missing` / `not_found` (R-FET-1–7).

7. **λ calibration?** **M7.** Envelopes and DefaultAgentSuccess do not wait on a numeric λ (R-COST-3). G1 still binds via byte/turn discipline.

8. **Which named workloads prove a TTY need?** **Proving class:** local programs whose observable behavior or byte stream depends on `isatty` (progress/spinner, color, pagers, prompts that fail immediately without a TTY). v0.1 **does not require shipping a TTY-class plugin.** Advertise-and-refuse or named absence (no valid `tty-oneshot` row on a completed default-catalog walk) is a complete R-REACH-1 story. Shipping a means waits on a plugin that exercises that class; helper topology stays design (R-TTY-7).

9. **Is live `run_tail` / `run_events` legal on a still-running run?** **No for v0.1** (R-QB-28). Agents wait with `wait_ms` / `await_runs` (G6). A yes would open the §20 live-observation gate, not enlarge oneshot TTY.

10. **How is optional-helper unreadiness advertised?** **`admission.tty_not_ready`** — request outcome, no `run_id`, `retryable=false` (R-TTY-6). Catalog `capability_class` is the class billboard, not health. R-REG-5 / `valid` remains source validation only. Absence = no valid `tty-oneshot` row after a full default-catalog walk. Failed run = terminal `RunView` after admit.

**Assumptions that remain risks (not open questions).** Automatic child runtime steers naive I/O into `work/` and `outputs/`; authors who `open()` absolute paths outside `work/` are still unmanaged (R-AUTO-7). A material slice of local work *may* be terminal-sensitive; absence/advertise-and-refuse covers installs that never ship a TTY plugin.

---

## 26. Spike evidence (2026-08-19)

See `spikes/RESULTS.md`.

| Spike | Result |
|---|---|
| M0 FastMCP 3.4.7 | stdio `tools/call` works; no list `ttlMs`; Tasks extra = Docket → forbidden |
| M0.5 macOS | after NumPy compute, 4 OS threads; interpreter warns on `fork`; **spawn-per-run** |
| M0.5 Linux | after NumPy compute, 16 OS threads; **spawn-per-run** |
| M0.6 | 62.9 MB `result.json` → 329-byte index → 114-byte summary; all cases pass |

**v0.1 is not blocked on process or projection architecture.** It is blocked on implementing M1 against this document.
