# Projects and Bulkheads — trestle-v01

**Node:** trestle-v01  
**Mode:** brownfield decomposition  
**Input:** [target-delta-trestle-v01.md](../target-delta-trestle-v01.md), freeze HLD

## Comparative capability bar

### Capability cards

| Unit | Owns (capability) | Does NOT own |
|------|-------------------|--------------|
| **trestle-m1-kernel-skeleton** | Runnable kernel loop: Admit mints Handle; Execute spawns wrapper+child; Project emits status frame; ndjson ledger; snapshot registry; PluginSurface + automatic runtime | Projection beyond status frame; fetch; query; recovery; wait; hot reload |
| **trestle-m2-bounding** | `result.index` + summary projection; capture limits; artifacts; pins; fetch; auto-promote `outputs/` | Cancel/recovery; query views; registry hot reload |
| **trestle-m3-durability** | Cancel/timeout; atomic evidence writes; crash recovery from ledger | Query backend; MCP wait; retention/GC |
| **trestle-m4-waiting** | `wait_ms` on run; `await_runs`; partial sequences for running runs | Tasks mapper; live run_tail on running runs |
| **trestle-m5-query** | Filesystem query backend; nine views; paging; `backend`/`as_of`; R-QB-28 | SQLite (M5.1); CLI `--sql` |
| **trestle-m6-extending** | Hot reload; `registry_version`; promotion budget; plugin drop-in | Retention policy; chaos/doctor |
| **trestle-m7-operations** | Retention/GC; `trestle doctor`/`recover`; chaos matrix; workflow benchmarks | New kernel ports or MCP tools |

### Neighbor differentiation matrix

| Seam | Producer | Consumer | Contract |
|------|----------|----------|----------|
| I01 | M1 Admit+Scheduler | M1 Execute/Conductor | WorkOrder after durable `created` |
| I02 | M1 Execute | M1 wrapper+child | RunSpec letter; LedgerCommand back |
| I03 | M2 Project | M5 query | Evidence layout + index handles |
| I04 | M1 ControlSurface | M1 FastMCP porch | Nine tools → ControlSurface map |

Units M2–M7 extend prior units in-process; bulkheads are **versioned contracts** at unit boundaries, not separate deployables.

### Composition proofs (keep-set walks)

**Walk A — Agent happy path (post M4):** MCP `run` → Admit → Scheduler → Execute → wrapper spawns child → child runtime wraps script → poll capture → ledger terminal → Project status frame (+ optional wait) → `query` → `fetch`. No step imports FastMCP in wrapper/child. Refusal never carries `run_id`.

**Walk B — Script author path (M1):** Drop plugin `.py` → registry snapshot → `@trestle` decorator → child cwd=`work/`, TMPDIR, outputs auto-promote → script imports PluginSurface only.

**Walk C — Operator path (M7):** SIGTERM → draining → grace → ledger fsync → `trestle doctor` → torn ledger → recover → idempotent replay.

**Walk D — Hostile plugin (M2 exit):** Flood stdout/events/return → capture limits → control plane responsive → summary/index bounded.

## Units (nodes)

| Slug | Milestone | Prerequisites | Exposes |
|------|-----------|---------------|---------|
| trestle-m1-kernel-skeleton | M1 | — | Runnable run → terminal RunView |
| trestle-m2-bounding | M2 | m1 | Bounded projection + fetch |
| trestle-m3-durability | M3 | m2 | Recovery + cancel |
| trestle-m4-waiting | M4 | m3 | wait_ms / await_runs |
| trestle-m5-query | M5 | m4 | Nine-view query |
| trestle-m6-extending | M6 | m5 | Hot reload |
| trestle-m7-operations | M7 | m6 | Doctor + chaos + retention |

## Bulkheads (interfaces)

| ID | Name | Owner unit | Consumers | HLD |
|----|------|------------|-----------|-----|
| I01 | Admit–Scheduler–Execute | M1 | M1 Conductor | [I01](hld/I01-admit-scheduler-execute.md) |
| I02 | Execute–Wrapper–Child | M1 | M1 child runtime | [I02](hld/I02-execute-wrapper-child.md) |
| I03 | Project–Query–Evidence | M2 (Project), M5 (query) | M5, M2 fetch | [I03](hld/I03-project-query-evidence.md) |
| I04 | MCP–ControlSurface | M1 | FastMCP adapter | [I04](hld/I04-mcp-control-surface.md) |

## Child manifest

Each unit lands as `projects/<unit-slug>/` via `project-pipeline` with `entry_profile: hld_and_plan`.

Interface obligations are frozen at portfolio gate; units MUST NOT widen MCP aperture or add Kernel ports without requirements amendment.
