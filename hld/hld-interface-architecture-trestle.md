# Trestle — Interfaces Architecture

Scope: system  
Node: trestle-kernel  
Mode: amend  
Amendment: script-foundation-seam  
Supersedes: none (this document remains the freeze working set; `tty-overlay-kinds` types remain; this amendment closes former open questions and binds automatic child runtime)

**Implementation freeze remains in force.** This document is the agreement. Code under `trestle/` is scaffolding, not authority.

## Guiding Light

Upstream calls this document must serve. Plain language; no requirement IDs.

**Upstream:** [Requirements (Draft v0.6)](../trestle-requirements.md)

### Why this project exists

- Agents should be able to write and run powerful local CLI tools and scripts without those tools flooding the context window; evidence stays on disk, and telemetry is available on pull.

### What must be true upstream

- A refused request must never look like a run that started and failed.
- Default agent replies stay small; full evidence stays on disk with honest accounting when bytes are suppressed.
- New capability should arrive as a plugin file, not as more MCP tools on every turn.
- Terminal-sensitive local work is optional — reachable under the same aperture or visibly excluded, never required core machinery.

### This document's job

- Freeze the Kernel agreement: three ports, nine tools, and envelope families agents and plugin authors rely on.

## Summary

Trestle is a **Kernel** (foundation) that keeps a durable execution ledger. FastMCP is a **porch**. **Scripts** are user callables wrapped by that foundation. The Kernel publishes **three ports** — Admit, Execute, Project — that scripts never see. Plugin authors see only PluginSurface: a `Context` protocol in a disposable child whose **process environment** makes the managed path the default (cwd=`work/`, `TMPDIR`, `outputs/` auto-promote). Untrusted plugin bytes enter the MCP process only as a bounded projection built from `result.index` plus `pread`.

## References

- [Requirements (Draft v0.7)](../trestle-requirements.md)
- [Spike decisions](../spikes/RESULTS.md)
- Exploration seeds (provisional; this HLD is authoritative): `_tmp/exploration-interface_design-cycle-1/H1-agent-envelope-hybrid.md`; `_tmp/exploration-interface_design-cycle-2/H1-catalog-envelope-first-failure.md` (H-dir-1: CatalogView + event-time `first_failure`)
- Complementary (cite; do not fork bodies): [TTY-class oneshot overlay HLD](hld-tty-overlay-trestle.md); [TTY-class oneshot overlay contract](interface-design-tty-class-trestle.md)

## Goals & Non-Goals

**Goals**

- A named consumer finishes its job from **one** public contract.
- Request refusals cannot be mistaken for runs (`RequestOutcome` has no `run_id`; `rejected` is not a run state).
- **DefaultAgentSuccess** is specified once (G4, R-BUD-5–8, R-BUD-12): status frame always; verbatim summary iff canonical bytes fit `summary_budget`; `next` handle when truncated or never fit; same grammar on `run` (after wait), `await_runs`, and any Tasks mapper — never a second default payload species.
- Plugin authors write one Python file on the **script** side of a named seam; they never receive a writable `evidence/` path and never import FastMCP or Kernel. The child **runtime** (foundation) steers naive stdlib I/O into the managed path (R-AUTO-1–7).
- R-INV-1 is a type-and-port law, not a comment in a middleware file.

**Non-goals**

- Relitigating spawn-per-run, quiet wrappers, poll/select capture, `result.index`, no Docket/Redis, POSIX-only. Wrapper topology is one quiet wrapper per published snapshot (R-EXEC-34). v0.1 does not ship a Tasks mapper (R-WAIT-4).
- Host health outside the hostile-plugin envelope.
- Nested plugin calls, `InputFile`, warm-import supervisors (v0.2).
- One MCP tool per plugin (R-MCP-1 forbids it).

**Constraints**

- MCP `2026-07-28` via FastMCP **stdio only** (pin 3.4.7). Wrappers and children must not import FastMCP.
- Ledger `ndjson` is recovery authority; `meta.json` is not.
- Fetch never accepts filesystem paths.

## Key Design Decisions

1. **The product is the Kernel, not FastMCP.** Colocating Kernel and adapter in one OS process is packaging (R-FMC-1). FastMCP must not hold `Run`, cache live execution, or become the executor (R-FMC-2, R-FMC-8).
2. **Three Kernel ports, two adapter-facing.** ControlSurface (CLI + MCP) composes Admit and Project. Door calls Admit only. Execute’s only consumer is Conductor. If CLI or MCP calls Execute, the topology has already failed.
3. **Public type families plus envelope sums.** `Handle`, `RequestOutcome`, `RunView`, `BoundedView`, `CatalogView`, `FetchSlice`; `AdmitResult` is a tagged union at the admit boundary. Bytes, paths, ledger records, and plugin objects are not public types. `BoundedView` is query pagination only; `CatalogView` is registry publication — they are not subtypes of each other.
4. **`wait_ms` is ControlSurface policy that composes Project.** `ControlSurface.run` owns `wait_ms` (default 2000 per R-WAIT-1); `AdmitRequest` has no `wait_ms`. Composition: Admit then `Project.await_one`. Not FastMCP Tasks.
5. **Catalog is a Project view**, not a fourth Kernel port. Registry publication is Kernel-private; `registry_version` is the freshness fact (FastMCP 3.4.7 has no `ttlMs`). `list_plugins` returns **`CatalogView`**, not `BoundedView`. MCP `tools/list.registry_version` **mirrors** `CatalogView.registry_version` on the same publication — drift is a **conformance failure**, not a second freshness channel.
6. **CLI may be richer than MCP.** `query --sql`, `doctor`, `recover`, verbosity live on OperatorContract only. Same verb, different admission rules across transports is forbidden.
7. **Freeze the agent aperture first.** ProjectionContract is Handle + RunView (DefaultAgentSuccess) + BoundedView + CatalogView + FetchSlice + fetch windows. Internals may churn behind it.
8. **Thin MCP definition (G1, R-MCP-1–3).** Hosts inject `tools/list` into the model every turn. The porch is nine **stable, small** tool definitions. Plugin JSON Schemas MUST NOT appear in `tools/list`, MUST NOT be inlined into `run`’s `inputSchema` (`args` stays an unstructured mapping; Admit validates after pick), and MUST NOT ride `list_plugins` (R-MCP-2). `describe_plugin` is pull-for-one (side-effect honesty, not a type dump). Tool count and definition bytes MUST NOT grow with plugin count, plugin schema width, or the TTY overlay. A porch that floods agent context has already failed this agreement.
9. **Foundation wraps scripts (R-BOUND-1–6).** PluginSurface is the only script-facing contract. Kernel ports, FastMCP, and ledger are not importable from plugin source. The child process hosts a foundation runtime and a script as distinct modules. Automatic filing is foundation behavior on that wrap, not a script SDK.

## Shared Foundations

### Consumers

| Role | Job | Sees |
|------|-----|------|
| **Door** | Refuse a request or mint a Handle — no wait, no projection | Admit only |
| **ControlSurface** | Orchestrate admit + optional wait; MCP/CLI agent verbs | Admit + Project |
| **Lens** | Wait, cancel, query, fetch, pin, catalog (via ControlSurface or direct Project) | Project |
| **Conductor** | Drive one admitted run to a terminal ledger record | Execute |
| **Scheduler** (Kernel-private) | Mint WorkOrder after durable `created`; enforce queue/concurrency/drain | WorkOrder → Conductor |
| **Plugin author** | Implement one callable | PluginSurface only (`Context` + stub + decorator) |
| **Child runtime** (foundation) | Wrap the script: cwd, fds, logging, index, auto-promote | Foundation internals; **not** script source |
| **Script** | User work in the child | PluginSurface; **not** Kernel, FastMCP, ledger |
| **Child** | One OS process for runtime + script | immutable `RunSpec` + `Context` |
| **Wrapper** | Spawn, capture, reap — no plugin imports | process-control letters, not Kernel types |

### Script / foundation seam (R-BOUND-1–6)

Two planes. The Kernel **wraps** scripts; scripts do not wrap the Kernel.

| Side | Public contract | Forbidden |
|------|-----------------|-----------|
| **Foundation** | Admit, Execute, Project, LedgerCommand, ControlSurface, OperatorContract | Importing or executing script modules in server or wrapper |
| **Script** | PluginSurface: `trestle` decorator, `Context`, `ArtifactRef`, child env (cwd / `TMPDIR` / fds) | Kernel types, FastMCP, ledger append, MCP tools, writable `evidence/` |

The child process contains **both**: a foundation runtime (Context implementation) and the script module. Those are different Python packages. Validation rejects script imports of foundation internals (R-PLUG-6). Stub `Context` substitutes for the runtime in tests; it does not substitute for the Kernel.

Automatic capture is **foundation-on-the-wrap**, not a second author API. Growing Context with Kernel verbs (query, pin, admit) is a seam failure.

### Request vs run

A **request** may die without creating a run. A **run** exists only after durable `created` on the ledger.

`RequestOutcome` and `RunView` are **distinct nominal families**. Identical JSON by coincidence is a bug. Putting `run_id` on a refusal is a crime.

```python
class RequestOutcome:
    code: str           # admission.* or projection.* namespace
    message: str
    retryable: bool
    origin: Literal["admission", "projection"]
```

**Code namespaces** — collision across namespaces is a defect:

| Namespace | Examples | MUST NOT appear when |
|-----------|----------|----------------------|
| `admission.*` | `plugin_not_found`, `invalid_args`, `queue_full`, `idempotency_key_conflict`, `service_draining`, `artifact_not_found`, `artifact_expired`, `artifact_missing`, `tty_not_ready` | A Handle was admitted |
| `projection.*` | `invalid_handle`, `expired`, `missing`, `cursor_expired`, `abandoned`, `not_found`, `invalid_args`, `invalid_view`, `not_finalized`, `cancel_accepted`, `pin_accepted`, `unpin_accepted` | Admitting new work |

`rejected` is not a run state and not an outcome code — refusals are `RequestOutcome` with **no** `run_id`.

Run states (internal names, projected as `RunView.state`): `queued`, `running`, `succeeded`, `failed`, `cancelled`, `timed_out`, `worker_exit`, `crashed`, `interrupted`. **`rejected` is not in this set.**

### Handle grammar

Opaque identifiers. Never filesystem paths on ControlSurface.

| Prefix | Meaning |
|--------|---------|
| `r_…` | run |
| `art_…` | artifact |
| view cursor | opaque paging token |
| fetch target | handle or `<run_id>/result` pseudo-artifact — still not a path |

Path-shaped strings → `projection.invalid_handle` (R-FET-8).

### DefaultAgentSuccess (G4, R-BUD-5–8, R-BUD-12)

The **single** agent-visible success grammar for terminal and running frames on `ControlSurface.run` (after wait), `await_runs`, and any optional Tasks mapper:

```
DefaultAgentSuccess ⊆ RunView
  ALWAYS: status frame (run_id, state, duration_ms, event_count, artifact_count,
          result_bytes, compact error slot, status_frame_version,     # R-BUD-13–17
          limits_exceeded)  # R-LIM-5; None/omit = not checked (any class);
                            # empty list = checked zero; non-empty = counted gaps (bytes_suppressed).
                            # TTY-class terminal MUST be a list; empty legal only after tty_console check
                            # (overlay: merge wrapper + child markers; never coerce pipe-only None to []).
                            # Class stays CatalogView.capability_class — not this field.
  IFF canonical summary bytes ≤ summary_budget: verbatim projected value            # R-BUD-5
  ELSE summary sub-grammar by root_type (R-BUD-6–7):
    object — whole-field skip in summary_fields order; never partial fields
    array  — {count, sample, handle}; sample is verbatim prefix within budget
    scalar — handle only (strings never truncated); None always fits
    nested objects — top-level fields only
  FOOTER (when summary truncated or skipped): next, result_bytes, truncated, omitted  # R-BUD-8
  Handle vs footer (per root_type — one continuation surface each):
    object (field-skip) — RunView.next only; summary has no embedded handle
    array  — summary.handle only; RunView.next MUST be absent
    scalar (oversize) — summary is the Handle; RunView.next MUST be absent
    verbatim — neither; truncated=false
  NON-TERMINAL (state ∈ {queued, running} OR evidence_finalized pending):
    summary MUST be None; truncated=false; next absent; omitted absent.
    Status frame only — no result sub-grammar until a result exists (R-STORE-12/13).
  NEVER: universal envelope wrapping both; never a second default payload species   # R-BUD-12
```

On `wait_ms` timeout while still executing: return a **running** status frame obeying the same grammar (non-terminal row above) — not a Task handle, not a different payload species (R-WAIT-1).

### Projection law (R-INV-1)

Only **Project** may emit toward the MCP process. It reads `result.index` and `pread` ranges. It never slurps `result.json`. Console, events, and artifacts reach agents only as Handle-addressed, budgeted slices.

### LedgerCommand (narrow)

Sole mouth that appends **run-history** to `ledger.ndjson`. Sequence numbers: Kernel only (R-RUN-10).

In scope: created, admitted, started, progress, artifact_declared, artifact_available, limit_exceeded, execution_ended, evidence_finalized, terminals, recovery suffix.

**Out of scope:** pin/unpin (`pins.json`), GC, registry publish, query ingest, SQL. Those have other Kernel-private writers. A mega-enum is the next god-object.

Child and wrapper do not speak LedgerCommand. They write their files; Kernel commands the ledger from bounded observations.

---

## External Interfaces

### Mutual sufficiency

| Consumer | Needs | Provider supplies | Sufficient when |
|----------|-------|-------------------|-----------------|
| Door | refuse or mint Handle | Admit | `admit` completes without Execute types or Project |
| ControlSurface | admit + optional wait + agent verbs | Admit + Project | `run` composes Admit then optional `await_one`; DefaultAgentSuccess on success paths |
| Lens | pictures of existing runs | Project | await/cancel/query/fetch/catalog complete without spawn types |
| Scheduler | queue slot + WorkOrder mint | Kernel-private Scheduler | WorkOrder minted only after durable `created` |
| Conductor | drive one WorkOrder | Execute | terminal ledger record without FastMCP or status frames |
| Author | one-file callable | Context protocol | pytest with stub; no evidence path; no FastMCP |
| Agent | G1-cheap work | ProjectionContract via MCP | default payload = DefaultAgentSuccess |
| Operator | diagnosis + recovery | OperatorContract | CLI can `--sql` / `doctor` without widening MCP |

If Door needs a pid to return from `run`, Admit is a lie. If Lens needs a Run object to cancel, Project is a lie. If ControlSurface admits with `wait_ms` on `AdmitRequest`, the composition law is broken.

---

### Admit

The Kernel port that turns a request into a refusal or a Handle. Exists so Door never waits, never projects, and never executes.

#### Summary

| Field | Value |
|-------|-------|
| Kind | protocol |
| Owner | Kernel |
| Consumers | Door (`Admit.admit` only); ControlSurface composes via `run` |
| Depends on | published plugin schema, Scheduler queue policy, idempotency store |
| Lifetime | stateless per request; durable effect is ledger `created` or nothing |
| Concurrency | Kernel serializes ledger sequence; admission itself is request-scoped |

#### Public surface

| Member | Signature | Description |
|--------|-----------|-------------|
| admit | `admit(req: AdmitRequest) -> AdmitResult` | Preconditions: service not draining for new work; plugin published; advertised `tty-oneshot` identity + Kernel-private unreadiness → `admission.tty_not_ready` (rule body: [overlay HLD](hld-tty-overlay-trestle.md); MUST NOT execute, wait, or return summaries to learn unreadiness); artifact refs resolve or `admission.artifact_not_found` / `artifact_expired` / `artifact_missing`. Postcondition: tagged `Refused` with **no** `run_id`, or tagged `Admitted` with Handle after durable `created`. Must not execute, wait, or return summaries. |

```python
class AdmitRequest:
    plugin: str
    version: str | None  # omitted = latest; identity recorded
    args: Mapping[str, object]
    idempotency_key: str | None  # TTL 3600; reconstructible from spec/ledger (R-WAIT-10–13)

class AdmitResult:  # tagged union — refuse vs run unrepresentable
    tag: Literal["refused", "admitted"]
    # iff tag == "refused":
    outcome: RequestOutcome  # admission.* only; no run_id field
    # iff tag == "admitted":
    run_id: Handle
```

`wait_ms` is **not** a field of `AdmitRequest`. Schema MUST NOT allow simultaneous `outcome` and `run_id` without a discriminant.

#### Invariants

- Durable `created` includes `spec_hash` and `service_epoch` (R-RUN-19).
- Collision on `run_id` regenerates; clock is not the sole uniqueness mechanism (R-RUN-2).
- Queue overflow is `admission.queue_full` — a RequestOutcome, not a run.
- Idempotency: same key + same plugin/snapshot/args_hash → same Handle; different args → `admission.idempotency_key_conflict`; keys reconstructible across restart (R-WAIT-10–13).

#### Error behavior

All refusals are tagged `Refused` with `RequestOutcome` (`admission.plugin_not_found`, `admission.invalid_args`, `admission.queue_full`, `admission.idempotency_key_conflict`, `admission.service_draining`, `admission.artifact_not_found`, `admission.artifact_expired`, `admission.artifact_missing`, `admission.tty_not_ready`, …). Thrown early at the boundary. Never a run state.

#### Extension points

New admission reasons add `admission.<code>` values. They do not add fields to `RunView`.

---

### Scheduler (Kernel-private)

The Kernel component that mints `WorkOrder` only after durable `created` and enforces execution queue policy. Exists so WorkOrder creation is named (Creator) and Door/Lens never see spawn machinery. **Not a fourth Kernel port** — Scheduler is Kernel-internal; Conductor is its sole Execute consumer.

#### Summary

| Field | Value |
|-------|-------|
| Kind | Kernel-private service |
| Owner | Kernel |
| Consumers | Conductor (receives WorkOrder) |
| Depends on | ledger `created` records, queue store, concurrency counters |
| Lifetime | process lifetime; hands one WorkOrder per admitted run |
| Concurrency | enforces caps below; joins do not occupy workers (R-WAIT joins via Project) |

#### Invariants

- **Mint timing:** WorkOrder minted only after LedgerCommand `created` is durable — never before, never from MCP (R-STORE-24, R-RUN-19).
- **Wrapper topology:** one quiet wrapper per published snapshot (R-EXEC-34); children remain spawn-per-run.
- **`max_active_runs`** default `min(8, cpu_count)` (R-EXEC-25).
- **Per-plugin concurrency** limits independent (R-EXEC-26).
- **Queue depth** bounded (256); excess → `admission.queue_full` at Admit, not a run (R-EXEC-27).
- **No run blocks on another run** in v0.1 (R-EXEC-28).
- **Drain:** on `SIGTERM`/`SIGINT`, service enters `draining` — new Admit returns `admission.service_draining`; queued-not-started rejected as admission outcomes; running children get `shutdown_grace_s` (30); remainder cancelled; finalize; fsync; terminate wrappers; exit (R-EXEC-32). `draining` is a **service** state, not a run state.

#### Error behavior

Scheduler does not surface MCP types. Queue saturation is visible only as `admission.queue_full` at Admit. Over-capacity is refused before `created`.

---

### Execute

The Kernel port that drives one admitted run to a terminal ledger record. Exists so spawn, capture, and child classification cannot leak into Door or Lens.

#### Summary

| Field | Value |
|-------|-------|
| Kind | protocol |
| Owner | Kernel / Conductor |
| Consumers | **Conductor only** |
| Depends on | snapshots, quiet wrapper, child, LedgerCommand |
| Lifetime | one WorkOrder; child never reused |
| Concurrency | wrapper single-threaded poll/select; no asyncio in wrapper |

#### Public surface

| Member | Signature | Description |
|--------|-----------|-------------|
| drive | `drive(order: WorkOrder) -> ExecutionEnded` | Precondition: run already admitted. Postcondition: `execution_ended` then `evidence_finalized` then exactly one terminal (happy path), or documented recovery suffix. Does not return plugin results. |

```python
class WorkOrder:
    run_id: Handle
    snapshot_id: str
    spec_hash: str
    # run-directory capability is Conductor-private — not an MCP type

class RunSpec:  # sealed letter to the child — not Admit/Project currency
    plugin: str
    version: str
    snapshot_id: str
    args: Mapping[str, object]
    args_hash: str
    source_sha256: str
    schema_sha256: str
    manifest_sha256: str
    python_version: str
    platform: str
    summary_budget: int
    timeout_s: int
    resolved_artifacts: Mapping[str, Handle]
    deadline: datetime  # absolute, set at admission
```

#### Invariants

- Wrapper does not import the plugin snapshot or third-party plugin deps (R-EXEC-6).
- Child is process-group leader; never forked from a plugin-warmed interpreter (R-EXEC-4).
- Evidence data does not pass through the MCP process (R-EXEC-13).
- Cancel tickets from Lens are `{run_id, flag}` — identifier-shaped. If the ticket grows a Run, the god-object arrived in the mail.

#### Error behavior

Child classification (R-EXEC-22): clean return → `succeeded`; uncaught exception → `failed`; `SystemExit`/nonzero → `worker_exit`; signal/OOM → `crashed`. A crashed child must not crash the server or unpublish tools (R-EXEC-23). Execute reports classification via LedgerCommand, not via MCP types.

#### Substitutability

Any Conductor implementation that honors WorkOrder → terminal ledger record is substitutable. MCP is never an Execute implementation.

---

### Project

The Kernel port that hands bounded pictures. Exists so G1 is a grammar, not a habit, and so R-INV-1 has a single crossing. **v0.1 Lens facade:** cancel, pin, and unpin remain on Project (E2 Control port deferred — see CAFE checklist).

#### Summary

| Field | Value |
|-------|-------|
| Kind | protocol |
| Owner | Kernel |
| Consumers | Lens (CLI + MCP ControlSurface verbs except `run` admission) |
| Depends on | ledger, index+pread, named views, pin store, registry (read) |
| Lifetime | request-scoped reads; pin/cancel have durable side effects owned elsewhere |

#### Public surface

| Member | Signature | Description |
|--------|-----------|-------------|
| status | `status(run_id: Handle) -> RunView \| RequestOutcome` | Status frame for an existing run. Missing handle → `projection.invalid_handle`. Postcondition: `state ∈ {queued, running}` until durable `evidence_finalized`; terminal `RunView` only after that record is durable (R-STORE-15). If `execution_ended` is durable but `evidence_finalized` is not, stall — return `running` frame, not terminal. |
| await_one | `await_one(run_id: Handle, wait_ms: int) -> RunView \| RequestOutcome` | Block without occupying a worker. Same terminal gate as `status` (R-STORE-15). Returns running or terminal frame obeying DefaultAgentSuccess. |
| await_many | `await_many(run_ids: Sequence[Handle], mode: JoinMode, timeout_ms: int) -> Sequence[RunView] \| RequestOutcome` | Join semantics per **JoinMode table** below. **Batch policy:** if **any** member would yield a `projection.*` `RequestOutcome` (including `projection.invalid_handle`, `projection.expired`, `projection.missing`, …), fail the **entire** call with that outcome — no partial sequence (throw-early; symmetric with `invalid_handle`). Otherwise partial on timeout per mode; each frame obeys DefaultAgentSuccess and the terminal gate. |
| cancel | `cancel(run_id: Handle) -> RequestOutcome` | Accepts a cancel **request**. Success → `projection.cancel_accepted` (`retryable=false`). Reply is Outcome, never a run state. |
| query | `query(view: ViewName, params: Mapping[str, object], cursor: Handle \| None) -> BoundedView \| RequestOutcome` | Named views only. No raw SQL on this port. Until durable `evidence_finalized` for the addressed run: only `run` and `recent_runs` succeed; other ViewNames → `projection.not_finalized` (`retryable=true`) (R-QB-28). |
| fetch | `fetch(target: Handle, window: FetchWindow) -> FetchSlice \| RequestOutcome` | Never paths. Default last 50 lines where applicable. Scan budgets apply. Window kind not in the handle-provenance permitted set → `projection.invalid_args` (no coercion). |
| pin / unpin | `pin(target: Handle) -> RequestOutcome` / `unpin(target: Handle) -> RequestOutcome` | Pin store, not ledger. Success → `projection.pin_accepted` / `projection.unpin_accepted`. Unpin of unpinned is success no-op (`projection.unpin_accepted`). |
| catalog | `list_plugins(...) -> CatalogView \| RequestOutcome` / `describe_plugin(...) -> Mapping[str, object] \| RequestOutcome` | Registry publication as **CatalogView** (not BoundedView). Not a fourth Kernel port. |

```python
JoinMode = Literal["all", "any", "first_failure"]
```

**JoinMode normative table** (R-WAIT-1–8) — one row per mode; partial on `timeout_ms` always returns `Sequence[RunView]` (never `RequestOutcome`).

| Mode | Success trigger | Member set on success | Timeout partial |
|------|-----------------|----------------------|-----------------|
| `all` | Every input `run_id` has a terminal `RunView` (R-STORE-15 gate) | One `RunView` per input `run_id`, **input order** | One `RunView` per input `run_id`, **input order**; terminal members → terminal frames; still-`queued` / still-`running` members → non-terminal status frames |
| `any` | At least one input `run_id` has a terminal `RunView` | One `RunView` per input `run_id`, **input order**; triggering member(s) terminal; others may be non-terminal | Same as `all` timeout partial |
| `first_failure` | Earliest **failure terminal** among members by **event time** (any member — not index-gated). Failure set: `state ∈ {failed, cancelled, timed_out, worker_exit, crashed, interrupted}`. Same event time → **lower input index** wins. Success does **not** require all members terminal. Clean all-terminal with no failure is also success (all succeeded / no failure terminal occurred). | One `RunView` per input `run_id`, **input order**; triggering failure member(s) terminal; others may be non-terminal | Same as `all` timeout partial |

Partial sequences **preserve input order** and **include still-running (or queued) frames** for members not yet terminal. Joins do not occupy workers (R-WAIT joins via Project).

**`first_failure` event-time clock:** comparable time is the **durable timestamp of the failure terminal ledger record** after the R-STORE-15 `evidence_finalized` gate — not wall-clock at join return, not wrapper pid times. Each `run_id` has one such timestamp once it is a failure terminal. Ties (equal timestamps) MUST break to the **lower input index**. `[slow_ok, fast_fail]` **succeeds** when `fast_fail` reaches a failure terminal even if `slow_ok` is still `running`.

```python
ViewName = Literal[
    "run", "last_error", "run_tail", "run_events", "recent_runs",
    "recent_failures", "run_provenance", "run_artifacts", "artifact_refs",
]

class ArraySummary:  # array truncated root only (R-BUD-6–7)
    count: int
    sample: Sequence[object]
    handle: Handle

class LimitExceededMarker:  # R-LIM-3
    stream: str  # wrapper pipe-capture: stdout / stderr; helper-transport holes (overlay): tty_console
    limit: str
    bytes_recorded: int
    bytes_suppressed: int

class RunView:  # DefaultAgentSuccess carrier on success paths
    run_id: Handle
    state: str  # run states only — never "rejected"
    status_frame_version: int
    duration_ms: int | None
    event_count: int
    artifact_count: int
    result_bytes: int | None
    truncated: bool
    omitted: Sequence[str] | None  # summary_fields skipped whole (R-BUD-6–8); absent when verbatim or non-terminal
    summary: Mapping[str, object] | ArraySummary | Handle | str | int | float | bool | Sequence[object] | None
    # Checkable union (DefaultAgentSuccess): Mapping = verbatim or object field-skip; ArraySummary = array truncated;
    # Handle = scalar oversize only (R-BUD-6; RunView.next MUST be absent); scalar literals = verbatim within budget.
    # Non-terminal frames: summary MUST be None (not empty mapping).
    error: Mapping[str, object] | None  # compact; no traceback
    next: Handle | None  # object field-skip footer only (R-BUD-8); absent for array/scalar handle grammars and non-terminal frames
    limits_exceeded: Sequence[LimitExceededMarker] | None  # R-LIM-5
    # None/omit = not checked (any class). Empty list = checked zero. Non-empty = counted gaps (bytes_suppressed).
    # TTY-class terminal MUST be a list; omit on that class = contract miss.
    # Overlay: Kernel merges wrapper stdout/stderr (console/*) and child tty_console (work/*) — R-STORE-6,
    # one LedgerCommand limit_exceeded promotion. Empty TTY-class list only after tty_console check;
    # never coerce pipe-only None to []. Non-empty TTY-class gap ⇒ evidence_finalized completeness=partial;
    # agent-visible equivalent remains this list. TTY terminus is fetch(art_…), not query(run_tail).
    # Class stays CatalogView.capability_class. Kernel MAY keep a private admit snapshot of class for MUST-write; that snapshot is not agent grammar.
    # After a tty-oneshot row is unpublished, an agent holding an old Handle reads completeness from the list on that run's terminal frame — not from current catalog, not from None-as-class.
    # MUST-write + stream token `tty_console`: overlay HLD (amendment `tty-class-overlay`) + interface-design. This freeze types the member (amendment `tty-overlay-kinds`).

class BoundedView:  # query pagination only (R-QB-27) — NEVER carries registry_version
    items: Sequence[ViewRow]  # row shape fixed per ViewName — see ViewRow freeze table
    next_cursor: Handle | None
    truncated: bool
    backend: str   # required; MUST NOT change row semantics (R-QB-2, R-QB-5–9)
    as_of: str     # required; filesystem oracle uses as_of=now for a single file

class PluginCatalogRow:  # catalog freeze — bumps CatalogView, not ViewRow table (R-MCP-2)
    name: str
    version: str
    description: str  # teaser only; describe_plugin owns depth
    valid: bool       # False when listed via list_plugins(invalid=True) (R-REG-5); not helper health
    capability_class: str | None  # always present: "tty-oneshot" or JSON null (no claim, not omitted); valid stays R-REG-5

class CatalogView:  # registry publication — sibling of BoundedView, not a subtype
    registry_version: int  # Kernel publication fact (R-REG-6)
    items: Sequence[PluginCatalogRow]
    next_cursor: Handle | None
    truncated: bool
```

**ViewRow freeze** (ProjectionContract; R-QB-26/27) — one normative table, not a second spec dump. `ViewRow` is a typed record per row; cells obey per-cell truncation (R-QB-25). **Catalog rows are not ViewRows** — they live on `CatalogView` / `PluginCatalogRow` and do not appear in this table.

| ViewName | Row fields (types) | Sort key |
|----------|-------------------|----------|
| `run` | `run_id: Handle`, `plugin: str`, `version: str`, `state: str`, `started_at: str`, `ended_at: str \| None` | single row |
| `last_error` | `run_id: Handle`, `event_seq: int`, `kind: str`, `message: str`, `at: str` | events oldest-first |
| `run_tail` | `run_id: Handle`, `line_no: int`, `text: str` | oldest-first within tail |
| `run_events` | `run_id: Handle`, `event_seq: int`, `kind: str`, `payload: object` | oldest-first |
| `recent_runs` | `run_id: Handle`, `plugin: str`, `state: str`, `started_at: str` | newest-first (`run_id` desc) |
| `recent_failures` | `run_id: Handle`, `plugin: str`, `state: str`, `failed_at: str` | newest-first (`run_id` desc) |
| `run_provenance` | `run_id: Handle`, `snapshot_id: str`, `spec_hash: str`, `args_hash: str`, `source_sha256: str` | single row |
| `run_artifacts` | `run_id: Handle`, `artifact_id: Handle`, `name: str`, `retention_class: str`, `state: str` | declaration order |
| `artifact_refs` | `artifact_id: Handle`, `producer_run_id: Handle`, `referrer_run_id: Handle` | producer first, then `run_id` asc |

```python
# FetchWindow — discriminated by kind; only the row's fields are valid (R-FET-1–7; ProjectionContract freeze).
class RangeWindow:
    kind: Literal["range"]
    start_line: int  # 1-based inclusive
    end_line: int    # 1-based inclusive
class HeadWindow:
    kind: Literal["head"]
    count: int = 50  # lines; default 50 (R-FET-1–7)
class TailWindow:
    kind: Literal["tail"]
    count: int = 50  # lines; default last 50 when omitted (R-FET-1–7)
class GrepWindow:
    kind: Literal["grep"]
    pattern: str      # line match; capped matches in GrepMatchesSlice (R-FET-1–7)
class JsonpathWindow:
    kind: Literal["jsonpath"]
    expr: str         # JSONPath on indexed result or artifact slice
FetchWindow = RangeWindow | HeadWindow | TailWindow | GrepWindow | JsonpathWindow
# Kernel-enforced budgets: max_scan_bytes 64 MB, max_scan_time_ms 2000 (R-FET-9)

# FetchSlice — discriminated record per window kind; source is always Handle-addressed, never a path (R-FET-8).
class TextSlice:       # window kind range | head | tail
    tag: Literal["text"]
    source: Handle
    lines: Sequence[str]
    fraction: Mapping[str, int]  # e.g. start_line, end_line, total_lines
    truncated: bool
    scan_bytes: int  # bytes actually scanned; mandatory (R-FET-9)
class GrepMatchesSlice:  # window kind grep
    tag: Literal["grep_matches"]
    source: Handle
    matches: Sequence[Mapping[str, object]]  # line_no, text; capped (R-FET)
    match_count: int
    truncated: bool
    scan_bytes: int
class JsonpathMatchesSlice:  # window kind jsonpath
    tag: Literal["jsonpath_matches"]
    source: Handle
    values: Sequence[object]  # capped samples
    truncated: bool
    scan_bytes: int
class BinaryMetaSlice:  # binary targets — metadata only (R-FET)
    tag: Literal["binary_meta"]
    source: Handle
    size: int
    mime: str | None
    truncated: bool
FetchSlice = TextSlice | GrepMatchesSlice | JsonpathMatchesSlice | BinaryMetaSlice
# Non-v0.1 fetch window kinds are out of freeze until ProjectionContract bump.
```

**Handle provenance → permitted `FetchWindow`** (continuation after DefaultAgentSuccess truncation; R-FET-1–8):

| Handle source | Target | Permitted `FetchWindow.kind` | Expected `FetchSlice` |
|---------------|--------|------------------------------|------------------------|
| `RunView.next` | run result continuation (object field-skip) | `jsonpath`, `range`, `head`, `tail` on `<run_id>/result` pseudo-artifact | `JsonpathMatchesSlice` or `TextSlice` per window |
| `summary.handle` | array body continuation | `jsonpath`, `range` on indexed array slice | `JsonpathMatchesSlice` |
| `summary` (scalar oversize) | scalar value | `jsonpath` on scalar node | `JsonpathMatchesSlice` |
| `art_…` / declared artifact | artifact bytes | `range`, `head`, `tail`, `grep` (text); binary → metadata only | `TextSlice`, `GrepMatchesSlice`, or `BinaryMetaSlice` |
| `<run_id>/result` | full canonical result | `range`, `head`, `tail`, `grep`, `jsonpath` | per window kind above |

`RunView.next` and `summary.handle` MUST NOT alias the same bytes for array roots (`next` absent). Object truncation uses `next` only; array/scalar use in-summary handle only.

**Window-kind enforcement:** `fetch(target, window)` MUST return `RequestOutcome` `projection.invalid_args` (`origin="projection"`, `retryable=false`) when `window.kind` is not in the permitted set for that handle's provenance class. MUST NOT coerce (e.g. `TailWindow` on `summary.handle` MUST NOT become jsonpath). Path-shaped `target` remains `projection.invalid_handle` (R-FET-8), not `invalid_args`.

**Scan-budget channel (R-FET-9, single channel):** hitting `max_scan_bytes` (64 MB) or `max_scan_time_ms` (2000) is **never** `isError` / `RequestOutcome`. Return the `FetchSlice` variant with `truncated=true` and `scan_bytes` set to bytes actually scanned. That pair **is** the `scan_budget_exceeded` condition — there is no `projection.scan_budget_exceeded` outcome code. Continuation: TextSlice → `RangeWindow(start_line=fraction.end_line+1, …)`; Grep/Jsonpath → narrower `pattern`/`expr` or a `range` window on the same handle (no grep/jsonpath cursor in v0.1). Retrying the same window MUST NOT be assumed to yield more.

#### Invariants

- Default tool result = **DefaultAgentSuccess** (G4 / §10.3; R-BUD-5–8, R-BUD-12).
- `summary.json` is derived; Project may emit it; it is reconstructible from index+pread.
- After `evidence_finalized`, no evidence mutation (R-RUN-17). **`status` / `await_one` / `await_many` terminal gate (R-STORE-15):** terminal `RunView` (`succeeded`, `failed`, `cancelled`, `timed_out`, `worker_exit`, `crashed`, `interrupted`) only after durable `evidence_finalized`. While `state ∈ {queued, running}` or finalize is pending after `execution_ended`, frames stay non-terminal (`running` or `queued`). Stall — never project terminal early.

#### Error behavior

Refusal `RequestOutcome` codes: `projection.invalid_handle`, `projection.expired`, `projection.missing`, `projection.abandoned`, `projection.not_found`, `projection.cursor_expired`, `projection.invalid_args`, `projection.invalid_view`, `projection.not_finalized`. Mutator **success** codes (same family, `isError: false` on the porch): `projection.cancel_accepted`, `projection.pin_accepted`, `projection.unpin_accepted`. Never protocol-only silence. Fetch of a path-shaped string is `projection.invalid_handle`. Fetch of a permitted-handle with a disallowed `FetchWindow.kind` is `projection.invalid_args`. Fetch of an **abandoned** artifact **succeeds** until retention expiry or collection (R-ART-3) — `abandoned` is a `run_artifacts` retention class, not a fetch hide flag; collected → `projection.expired`. Scan-budget overflow is **not** a RequestOutcome — see scan-budget channel above. **`await_many`:** any member `projection.*` **refusal** (`invalid_handle`, `expired`, `missing`, …) fails the entire batch (no partial sequence); timeout partial is per JoinMode table only. Success codes are not batch-join members.

#### Extension points

New named views are a ProjectionContract version bump. `plugin_stats` is not a v0.1 view.

---

### ControlSurface / OperatorContract / ProjectionContract

Three widths, one Kernel.

| Contract | Width | Frozen? |
|----------|-------|---------|
| **ProjectionContract** | Handle, RunView (DefaultAgentSuccess), BoundedView, CatalogView, FetchSlice, fetch windows, `status_frame_version`, per-ViewName row shapes, PluginCatalogRow | **Yes** — agent floodgate |
| **ControlSurface** | `run`, `await_runs`, `cancel`, `query(view, params)`, `fetch`, `pin`/`unpin`, catalog | Both transports honor; MCP may lag |
| **OperatorContract** | ControlSurface **plus** CLI-only: `query --sql`, `doctor`, `recover`, verbosity | CLI may churn; `--sql` never on ControlSurface |

`adapter_coverage`: explicit map MCP tool name → ControlSurface member. Missing row = authorized lag. Same verb, different admission = forbidden drift.

#### `ControlSurface.run` composition (R-WAIT-1)

`wait_ms` lives **only** on `ControlSurface.run` (default 2000). Not on `AdmitRequest`. Not FastMCP Tasks.

```text
run(req, wait_ms=2000):
  result = Admit.admit(AdmitRequest without wait_ms)  → AdmitResult  # Kernel-internal tagged union
  if result.tag == "refused": return result.outcome     # admission.*; no run_id
  if wait_ms is None or wait_ms == 0: return Project.status(result.run_id)
  return Project.await_one(result.run_id, wait_ms)      # running frame on timeout
```

Returns: `RequestOutcome` (refused at admit) **or** `RunView` obeying DefaultAgentSuccess (status, optional wait, or running frame on timeout). Same grammar as `await_runs`.

**MCP wire grammar (`run` tool):** porch flattens `AdmitResult` — emit **`RequestOutcome | RunView` only**. Refusal → `isError: true` + `RequestOutcome` (`admission.*`; no `run_id`). Success path → `RunView` (status or post-wait frame). **Do not** emit `AdmitResult` tags, bare Handles, or Handle-only admit payloads on the wire unless a frozen JSON schema is also published; flatten at the porch.

---

### PluginSurface (`trestle.Context`)

Not a Kernel port. **This is the entire script-facing foundation.** Exists so a plugin author can be correct with a stub and one file — including a larger script that never calls MCP or ledger APIs — without depending on Kernel modules.

#### Summary

| Field | Value |
|-------|-------|
| Kind | protocol |
| Owner | child runtime |
| Consumers | plugin code; pytest stub |
| Depends on | work namespace, event sink, cancel flag |
| Lifetime | one child / one run |
| Concurrency | child may use asyncio; wrapper must not |

#### Public surface

| Member | Signature | Description |
|--------|-----------|-------------|
| artifact | `artifact(...) -> Path` | Staging path under `work/artifact-staging/<id>.partial`. Traversal rejected. Never `evidence/`. |
| attach | `attach(path: Path, ...) -> Handle` | Promotes into evidence; returns opaque handle. |
| copy_artifact | `copy_artifact(handle: Handle) -> Path` | Writable copy in `ctx.tmp`. Never an evidence path. |
| retain | `retain(handle: Handle) -> None` | Reachability pin from this run. |
| run_cmd | `run_cmd(...) -> CmdResult` | Process-group child; tails capped 2 KB for plugin logic. Not required for ordinary subprocess console capture (R-AUTO-5). |
| log / progress | event sinks | Write `events.ndjson` (framework also installs logging handler). |
| tmp | `Path` | `work/tmp`. Also `TMPDIR`. |
| outputs | `Path` | `work/outputs` keeper tree. Regular files remaining at `execution_ended` are auto-promoted (R-AUTO-3, R-ART-17). |
| cancelled | `bool` | Becomes true on cancel flag. |
| deadline | `datetime` | Absolute. |

#### Invariants

- No writable evidence path is passed to plugin code (R-STORE-3).
- DIP: plugin depends on this protocol, not Kernel classes, not FastMCP (R-FMC-5 wraps, does not subclass). R-BOUND-2: script import allowlist is PluginSurface only.
- Missing sinks must not be “solved” by growing scheduler/query/pin APIs onto Context (that would collapse the seam).
- **Automatic path (R-AUTO-1–7):** child cwd is `work/`; snapshot dir is not cwd and is not writable; `TMPDIR` is `work/tmp`; `work/outputs/` and `work/tmp/` exist at start; fd 1/2, stdlib logging, and `result.index` require no author action; stdlib subprocess that does not start a new session stays in-group and inherits captured fds. Stub Context MUST apply the same cwd / TMPDIR / outputs / logging semantics.
- Auto-promote is the same atomic path as `attach`. It does not add a handle prefix or MCP tool. Scratch under `tmp` and undeclared files elsewhere under `work/` are not auto-promoted (R-STORE-4).
- Automatic is not a host sandbox (R-AUTO-7).

#### Error behavior

Throw early on traversal, missing artifact, hijacked logging (recorded, not necessarily a run failure — R-CTX-5). Unmanaged `open()` outside `work/` is out of contract (R-SCOPE-2). Auto-promote overflow is limit markers (R-LIM-3), not silent drop.

---

## MCP surface (ControlSurface projection)

Excellence is agent behaviour under uncertainty (`helper-mcp-design` Performance Contract). Nine tools. Core count does not grow with plugins.

### Thin `tools/list` (definition budget)

`tools/list` is definition, not catalog. The scarce resource is the agent context window (G1).

| May appear on `tools/list` | MUST NOT appear on `tools/list` |
|----------------------------|----------------------------------|
| The nine ControlSurface tools, each with a **small** `inputSchema` that names Kernel fields (`plugin`, `version`, `args` as object, `wait_ms`, handles, view names) | Per-plugin JSON Schema, one tool per plugin, union-of-all-plugin-args, helper/Forge protocol, TTY/PTY fields |
| Short side-effect descriptions (R-MCP-3) | Restated type trees; `list_plugins` payloads; `describe_plugin` bodies |
| `registry_version` mirror (R-REG-6) | Plugin argument schemas as a second catalog |

`run.args` is `Mapping[str, object]` on the porch. Plugin schemas are Kernel-private until Admit (`admission.invalid_args`). Agents that need one plugin’s depth call `describe_plugin` (or `list_plugins` for names/class only). Optional MCP resources (R-MCP-9) MUST NOT become a second schema dump.

### Primitive routing

| Primitive | Control | Why |
|-----------|---------|-----|
| Tools: `run`, `await_runs`, `cancel`, `query`, `fetch`, `pin`, `unpin`, `list_plugins`, `describe_plugin` | Model-controlled | Side-effecting or computed actions |
| Resources: view catalog (optional) | Application-driven | Read-only catalog of named views (R-MCP-9) |
| Prompts | none in v0.1 | Not a substitute for tools |
| Sampling | **MUST NOT** | R-SCOPE-3 |
| FastMCP `task=True` / Docket | **MUST NOT** | R-FMC-8 |

### Semantic Contract Table

| Tool | When pick me vs neighbour | Preconditions | Postconditions | Idempotency / destructiveness | Forbidden confusions |
|------|---------------------------|---------------|----------------|-------------------------------|----------------------|
| `run` | I **create** work (or refuse) and may **wait**. Neighbour `await_runs` only joins existing Handles. | Plugin published; not draining; advertised `tty-oneshot` + Kernel-private unreadiness → `admission.tty_not_ready` (overlay HLD; do not execute to learn unreadiness) | Wire: `RequestOutcome` (`isError: true`; `admission.*`; no `run_id`) **or** `RunView` (status / post-wait; DefaultAgentSuccess). Porch flattens Kernel `AdmitResult` — no admit tags on wire. Terminal `RunView` only after durable `evidence_finalized` (R-STORE-15). | Idempotency key + TTL 3600 when provided; conflict if reused with different args (R-WAIT-10–13) | Not a per-plugin tool; not an inlined plugin schema on `tools/list`; not Tasks; `wait_ms` not on AdmitRequest; not bare Handle admit |
| `await_runs` | I **join** existing Handles | All run Handles valid for projection | `Sequence[RunView]` — DefaultAgentSuccess; terminal gate per R-STORE-15; JoinMode table. **Any** member `projection.*` refusal → entire call fails with that outcome (no partial batch). Timeout → partial per JoinMode (input order; includes still-running frames). | Read/wait; not a worker | Not a poll-spam substitute for `wait_ms` on `run`; not FastMCP Tasks |
| `cancel` | I **request stop** of an existing run | Handle exists | `RequestOutcome` `projection.cancel_accepted` (`isError: false`) / `projection.invalid_handle` (`isError: true`) | Cancel request is not the terminal state | Not `Run.cancel()`; reply is not a run state |
| `query` | I want a **named view** | view+params; evidence-stream views require `evidence_finalized` | BoundedView (`backend`, `as_of` required) | Read-only | Not `--sql`; not a schema dump (`list_plugins` has no schemas); overlay: `run_tail` is wrapper pipes, not TTY-class terminus; live tail of a running run is `projection.not_finalized` (R-QB-28) |
| `fetch` | I want **bytes of one handle** (budgeted) | opaque handle | `FetchSlice` (incl. `truncated=true` + `scan_bytes` on budget overflow) **or** `projection.invalid_handle` / `projection.invalid_args` | Read-only | Not a path; not slurping `result.json`; not `isError` for scan-budget overflow; not coercing window kinds; overlay: TTY console is `art_…`, not wrapper `console/*` |
| `pin` / `unpin` | I change **retention**, not run state | handle | `projection.pin_accepted` / `projection.unpin_accepted` (`isError: false`) | unpin unpinned = `projection.unpin_accepted` | Not LedgerCommand |
| `list_plugins` | I need **names**, not schemas | — | `CatalogView` (`registry_version` + `PluginCatalogRow` items). Porch `tools/list.registry_version` MUST equal this integer on the same publication. | Read-only | Not `describe_plugin`; not `BoundedView`; not schemas |
| `describe_plugin` | I need **one plugin’s** description + side-effect honesty | name | description; no provenance over unmanaged effects | Read-only | Not a schema dump; not `run` |

`run` + `wait_ms`: ControlSurface admits via Door (`Admit.admit`), then Lens (`Project.await_one`) until timeout or terminal. On timeout: **running** status frame (`summary=None`, `truncated=false`, `next`/`omitted` absent) — not a Task handle. MCP porch flattens to `RequestOutcome | RunView` (no `AdmitResult` tags). Agent must not learn `rejected` as a run state if Admit refused — that wire response has no `run_id` and uses `admission.*` codes only.

### Dual-error contract

| Failure class | Channel | Example | Agent next action |
|---------------|---------|---------|-------------------|
| Validation / business | `CallToolResult` `isError: true` + `RequestOutcome` | `admission.invalid_args`, `projection.invalid_handle`, `admission.queue_full`, `projection.not_finalized` | Fix args / pick another tool / `await_runs` until terminal |
| Fetch scan budget (R-FET-9) | Success `FetchSlice` `truncated=true` + `scan_bytes` | 64 MB or 2000 ms cap | Continue with next range / narrower window — not `isError` |
| Unknown tool / malformed JSON-RPC | Protocol error | typo’d tool name | Host-level; may not reach model |
| Plugin exception after admit | `RunView` terminal `failed` + compact error | plugin raised | Diagnose via `query`/`fetch`; do not map to protocol error |
| Crash / interrupt | `RunView` `crashed` / `interrupted` | OOM, SIGTERM drain remainder | Not auto-retry `interrupted` (R-RUN-5) |

Mutators that agents retry (`run` with idempotency key, `pin`) must be idempotent as specified. List/read payloads bounded; fetch scan overflow signals **`FetchSlice.truncated=true` with `scan_bytes` set** — never `projection.scan_budget_exceeded` as `RequestOutcome`.

### Mis-invocation playbook

| Mistake | Result | Safe next |
|---------|--------|-----------|
| `fetch("/tmp/…")` | `projection.invalid_handle` isError | Use Handle from status/query |
| `fetch(array summary.handle, TailWindow)` | `projection.invalid_args` isError | Use a permitted kind from the provenance table (`jsonpath` or `range`) |
| `query` with SQL string | `projection.invalid_args` (or `projection.invalid_view`) | Use a `ViewName`; SQL is OperatorContract CLI-only |
| `query(run_tail)` (or other evidence-stream view) before `evidence_finalized` | `projection.not_finalized` isError (`retryable=true`) | `await_runs` / `wait_ms`; then query |
| `await_runs` with any member `projection.*` refusal (invalid, expired, missing, …) | entire call fails with that `projection.*` outcome — no partial frames | Fix or drop offending ids before batch join |
| `run` while draining | `admission.service_draining` no `run_id` | Do not poll-create; wait or stop |
| `cancel` on never-admitted id | `projection.invalid_handle` | Check Admit result |
| Calling a plugin name as an MCP tool | unknown tool (protocol) | `run` with `plugin=` |
| Reusing idempotency key with different args | `admission.idempotency_key_conflict` | New key or same args |

### Composition & safety

Localhost, single user, plugin authorship is not a privilege boundary. Still: no FastMCP session as run store; no path fetch; no token passthrough (N/A remote). Annotations (`readOnlyHint` etc.) must match enforcement: `query`/`fetch`/`list_plugins`/`describe_plugin` read-only; `run`/`cancel`/`pin` are not.

### Scenario Proof Pack

**S1 golden** — User: “run `echo` with `{"text":"hi"}` and tell me when it finishes.”  
Trajectory: `list_plugins` → `run`(wait_ms large enough) → `RunView.state=succeeded` obeying DefaultAgentSuccess (status frame + verbatim summary iff R-BUD-5; footer `omitted`/`next`/`truncated` per R-BUD-8 when not verbatim). Success: `run_id` appears only inside `RunView` on success path; wire payload is `RunView`, not `AdmitResult`; terminal only after `evidence_finalized`; no evidence dump; same grammar as `await_runs`.

**S2 wrong-tool** — Agent calls a plugin name as a tool. Expect protocol unknown-tool or honest “use `run`”. Recovery: `run`.

**S3 validation** — `run` missing required arg. Expect `isError` + `RequestOutcome` `admission.invalid_args`, **no** `run_id`, no admit tags on wire.

**S4 pagination** — `query(recent_runs)` with cursor. Expect `BoundedView.next_cursor` or `projection.cursor_expired` on stale. Envelope includes `backend` and `as_of`.

**S4b catalog freshness** — `list_plugins` returns `CatalogView` with `registry_version=N` and `PluginCatalogRow` names (no schemas). After hot reload, next `list_plugins` returns `registry_version=N+1`. Agent detects staleness from the Project contract alone. Same publication: porch `tools/list.registry_version` MUST equal `N` / `N+1`. Injected porch mismatch is a **conformance failure**, not a second freshness channel.

**S5 idempotent retry** — same idempotency key + same args after timeout. Expect same `run_id` in `RunView` (not a second run); reconstructible across restart (R-WAIT-10–13).

**S6 path fetch** — `fetch` with `/Users/…`. Expect `projection.invalid_handle`, not file contents.

**S6b wrong window kind** — after S8, `fetch(summary.handle, TailWindow(kind="tail", count=50))`. Expect `isError` + `RequestOutcome` `projection.invalid_args` (not a `TextSlice`, not coerced jsonpath). Recovery: `JsonpathWindow` or `RangeWindow` per provenance table.

**S7 refuse vs run** — `queue_full`. Expect `isError` + `RequestOutcome` `admission.queue_full`, no `run_id`, no ledger row, no run-shaped payload.

**S9 mixed await batch** — `await_runs` with one valid and one member that would refuse (`projection.invalid_handle`, `projection.expired`, …). Expect entire call fails with that `projection.*` outcome; no partial `RunView` sequence.

**S9b first_failure mixed terminal order** — `await_runs([slow_ok, fast_fail], mode="first_failure")`. `fast_fail` reaches a failure terminal (durable timestamp after `evidence_finalized`) while `slow_ok` is still `running`. Expect **success** (not timeout-only); sequence **input order**: `[RunView_slow_ok running, RunView_fast_fail failed]`. Falsifies index-0 / “first input” reading. Same event time on two failures → lower input index is the trigger; both frames still present in input order.

**S8 hostile result (array)** — plugin returns huge array. Trajectory: `run`(wait_ms large enough) → `RunView` with `truncated=true`; `summary=ArraySummary(count=…, sample=[…], handle=…)` (verbatim sample prefix only; R-BUD-7); `RunView.next` absent (array root); `omitted` absent → `fetch(summary.handle, JsonpathWindow(kind="jsonpath", expr="$.[*]"))` → `JsonpathMatchesSlice` with capped `values`, `truncated=true`, `scan_bytes` set (success path, not `isError`) per R-FET-9. Success: agent recovers body without `result.json` slurp; handle provenance table matches array row.

**S8b hostile result (object)** — plugin returns huge object. Trajectory: `run`(wait_ms large enough) → `RunView` with `truncated=true`; `omitted` lists skipped top-level field names; `summary` is partial `Mapping[str, object]` (whole fields only; R-BUD-7); `summary.handle` absent; `RunView.next` present (object field-skip footer; R-BUD-8) → `fetch(run_view.next, JsonpathWindow(kind="jsonpath", expr="$.skipped_field"))` → `JsonpathMatchesSlice` with capped `values`, `truncated`/`scan_bytes` per R-FET-9 (success path). Success: agent continues via `RunView.next` only — not `summary.handle`; handle provenance table matches object row.

**S10 mutator success** — `cancel` on a running Handle → `isError: false` + `RequestOutcome` `projection.cancel_accepted` (not a run state). `pin` then `unpin` on an artifact Handle → `projection.pin_accepted` then `projection.unpin_accepted`; second `unpin` still `projection.unpin_accepted`.

### Host-boundary annex

| Server-controllable | Host assumption |
|---------------------|-----------------|
| **Thin** nine-tool `tools/list` (no per-plugin schemas) | Host injects `tools/list` into the model; a fat definition is a Trestle defect, not a host bug |
| Schemas of **Kernel** tool fields, `isError` taxonomy, Handle grammar, payload bounds | Host forwards `isError: true` content to the model |
| `wait_ms` / `await_runs` duration | Client holds long stdio `tools/call`. **v0.1 does not ship a Tasks mapper** (R-WAIT-4). If a later named client cannot hold the call, a Trestle-owned mapper — never Docket — is a new amendment |
| `registry_version` | Host refreshes `tools/list`; `tools/list_changed` is optimization. **Mirror:** `tools/list.registry_version` MUST equal `CatalogView.registry_version` on the same Kernel publication. Drift is a conformance failure, not an agent-visible second channel. |
| No sampling | Host must not require sampling for core jobs |

---

## Internals (proof that the agreements are realizable)

Packaging: FastMCP process may host Kernel + McpAdapter. Wrapper and child are other processes. Adapter owns **zero** durable files.

Happy path: Admit → LedgerCommand `created` → Scheduler mints WorkOrder → Conductor → spawn wrapper → child writes events/result/index under names it owns → Execute observes → LedgerCommand finalizes → Project builds RunView from index+pread obeying DefaultAgentSuccess.

Drain (R-EXEC-32): service state `draining` — not a run state. New Admit returns `admission.service_draining`. Queued-not-started rejected as admission outcomes. Running children get grace; remainder cancelled; wrappers terminated.

---

## Consumer Scenario Proof Pack (architecture)

| Scenario | Proof |
|----------|-------|
| Intended use | Agent completes a job with `list_plugins` (`CatalogView`) + `run` + optional `fetch`/`query`/`await_runs`; author tests with stub Context; DefaultAgentSuccess on all success paths. |
| Likely misuse | Path fetch, SQL on MCP (`projection.invalid_args`), disallowed `FetchWindow.kind` (`projection.invalid_args`), `rejected` as run state, `await_runs` batch with any member `projection.*` refusal, XOR AdmitResult on Kernel admit — each has a typed refusal. Scan-budget overflow is truncated `FetchSlice`, not a refusal. Treating porch `tools/list` as a competing freshness integer is a conformance bug. Inlining plugin JSON Schema into `tools/list` or `run` `inputSchema` is a G1 / R-MCP-1–2 miss. |
| Extension | New plugin: no new MCP tool; `registry_version` ticks. New catalog column: PluginCatalogRow + CatalogView bump (nine ViewRows untouched). New query view: ViewRow + ProjectionContract bump. New admission code: `admission.*` only. |
| Substitution | CLI and MCP both implement ControlSurface; Execute implementations swap behind WorkOrder. Two Project impls agree on CatalogView integer + PluginCatalogRow and on `[slow_ok, fast_fail]` first_failure success. |
| Internal change | Swap filesystem query backend; FastMCP pin bump; wrapper pooling — ControlSurface types unchanged. Adapter must not store Run. Registry publication internals stay Kernel-private. |

---

## CAFE / helper-ground checklist

- **Coherence:** each port has one reason to change (Admit=request fate, Execute=process, Project=pictures, Context=author sinks). Scheduler owns queue mint policy — not a fourth port. `BoundedView` = ledger pagination; `CatalogView` = registry publication.
- **Adaptability:** plugins, views, fetch kinds, catalog columns, and admission codes extend via envelope rows (`AdmitResult`, namespaced `RequestOutcome`, `FetchSlice`, ViewRow, PluginCatalogRow) — not by widening Execute onto MCP. Catalog column bumps do not bump nine query ViewRows.
- **Freedom:** callers do not need pids, NDJSON layout, or FastMCP internals. Catalog staleness is `CatalogView.registry_version` on the public Project contract.
- **Encapsulation:** bytes, paths, ledger records, BLAS thread pools stay behind ports; refuse cannot masquerade as run (tagged `AdmitResult`). Failure join order uses durable terminal timestamps, not wrapper clocks.
- **ISP:** `--sql` off ControlSurface; catalog not a fourth port; Context has no query/pin/scheduler. **Project SRP debt (accepted v0.1):** cancel/pin remain on Project/Lens facade; E2 Control port deferred — do not tick ISP false-positive; pictures-only consumers may justify Control split post-v0.1.
- **DIP:** Door depends on Admit only. ControlSurface depends on Admit + Project abstractions, not Execute. **Script depends on PluginSurface, not Kernel** (R-BOUND-2). Child runtime may depend on Kernel; script source must not.
- **Tightness:** verification of Admit is “tagged Refused or Admitted Handle”; it must not grow with wrapper internals. Verification of the MCP porch is “nine small Kernel tool definitions”; it must not grow with plugin schema width.

---

## Closed decisions (former open questions)

These do not block and **are no longer open**. Requirements v0.6 §25 is authority.

1. **Stdio hold time / Tasks mapper.** v0.1 wait is `wait_ms` / `await_runs` on stdio. **No Tasks mapper** (R-WAIT-4). Docket remains forbidden. A named client that cannot hold the call is a later amendment — it does not change these ports.
2. **Abandoned artifacts.** `fetch` of abandoned **succeeds** until retention expiry or collection (R-ART-3). Abandoned is a `run_artifacts` retention class, not a fetch hide flag. Collected → `projection.expired`.
3. **Wrapper pooling.** One quiet wrapper per published snapshot (R-EXEC-34). Not per run; not shared across snapshots.

Also bound here (requirements §25): `registry_version` remains freshness even if `ttlMs` appears; nine views are sufficient; no `plugin_stats`; no live evidence-stream query (`projection.not_finalized`, R-QB-28); unreadiness is `admission.tty_not_ready`; λ waits on M7; TTY proving class is `isatty`-sensitive oneshot without requiring a shipped plugin.

---

## Handoff (to decomposition / implementation)

Preserve: three Kernel ports; **script/foundation seam** (PluginSurface only on the script side; R-BOUND-1–6); ControlSurface composes Admit+Project; Door = Admit only; envelope type families + tagged `AdmitResult`; Kernel-private Scheduler; LedgerCommand run-history-only; Context not a Kernel port (**automatic child runtime**: cwd=`work/`, `ctx.outputs` auto-promote, R-AUTO-1–7); FastMCP porch; ProjectionContract freeze (DefaultAgentSuccess + CatalogView + BoundedView with `backend`/`as_of`; R-QB-28); `wait_ms` on ControlSurface.run only; **no v0.1 Tasks mapper**; catalog = Project `CatalogView`; `first_failure` = event-time any-member; porch `registry_version` mirrors CatalogView; E2 Control port deferred; nine MCP tools; **thin `tools/list`** (plugin schemas not inlined; G1 / R-MCP-1–3).

Overlay kinds (additive on freeze envelopes; not a tenth tool): `PluginCatalogRow.capability_class` (always present; `null` = no claim); `admission.tty_not_ready`; `RunView.limits_exceeded` (R-LIM-5 markers per R-LIM-3; overlay merge of wrapper + child). Semantics: [overlay HLD](hld-tty-overlay-trestle.md), [overlay contract](interface-design-tty-class-trestle.md).

Do not implement until this agreement is the working set. Existing `trestle/` scaffolding is not a license to invent a Run DTO that crosses ports or optional-field XOR on `AdmitResult`.

---

## Changelog

- **2026-08-20 / `script-foundation-seam`** — Requirements v0.7. Named two planes: foundation wraps scripts; PluginSurface is the only script-facing contract (R-BOUND-1–6). Child process hosts runtime + script as distinct modules. Consumers table splits Child runtime vs Script.
- **2026-08-20 / `closed-oqs-auto-runtime`** — Requirements v0.6. Closed former open questions (no v0.1 Tasks mapper; abandoned fetch succeeds; one wrapper per snapshot; R-QB-28 `projection.not_finalized`; BoundedView `backend`/`as_of`). PluginSurface automatic child runtime: `ctx.outputs`, cwd=`work/`, TMPDIR, auto-promote (R-AUTO-1–7). Overlay kinds unchanged.
- **2026-08-20 / `tty-overlay-kinds`** — Thin MCP definition: `tools/list` is nine small Kernel tool schemas; plugin JSON Schema stays off the porch (not inlined into `run`, not on `list_plugins`); `describe_plugin` remains pull-for-one. Handoff preserve list plus this line.
- **2026-08-20 / `tty-overlay-kinds`** — `capability_class` always present (`null` = no claim, not omitted); `limits_exceeded` comments bind Kernel merge of wrapper `console/*` + child `work/` `tty_console` (R-STORE-6; empty TTY list only after that check; TTY terminus `fetch(art_…)`, not `query(run_tail)`). Overlay amendment remains `tty-class-overlay`.
- **2026-08-20 / `tty-overlay-kinds`** — `limits_exceeded` None/omit = not checked (class stays CatalogView); Admit/`run` preconditions cite overlay `admission.tty_not_ready` without execute-to-learn; `LimitExceededMarker.stream` notes wrapper stdout/stderr vs overlay `tty_console`. Overlay HLD amendment remains `tty-class-overlay`; this freeze envelope-typing amendment remains `tty-overlay-kinds`.
- **2026-08-20 / `tty-overlay-kinds`** — Typed overlay-bound members onto freeze envelopes: `RunView.limits_exceeded` (R-LIM-5 + R-LIM-3 marker), optional `PluginCatalogRow.capability_class` (CatalogView bump; nine ViewRows untouched), `admission.tty_not_ready` on the `admission.*` example list. Requirements reference v0.5. Pointers to complementary overlay HLD and interface-design artifact. Handoff preserve list plus overlay kinds. Architecture Reference unchanged: three ports, nine tools, E2 Control deferred, DefaultAgentSuccess grammar, `first_failure`, CatalogView vs BoundedView split.
