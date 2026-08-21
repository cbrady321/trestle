# System HLD — trestle-v01 (program scope)

**C-id:** C-TRESTLE-V01-SYS  
**Authority:** [hld/hld-interface-architecture-trestle.md](../../../hld/hld-interface-architecture-trestle.md) (freeze)

## Composition

The v0.1 Kernel is delivered across seven units (M1–M7). The freeze HLD defines:

- Three ports: **Admit**, **Execute**, **Project**
- ControlSurface composes Admit + Project; Execute consumed only by Conductor
- Nine MCP tools; ProjectionContract as agent floodgate
- Spawn-per-run; one quiet wrapper per published snapshot

Program units slice the freeze HLD by **milestone exit oracles**, not by port ownership — M1 establishes all three port skeletons; later units harden subsystems.

## Bulkhead index

| Bulkhead | Span | First landing |
|----------|------|---------------|
| I01 Admit–Scheduler–Execute | Admission → WorkOrder → Conductor | M1 |
| I02 Execute–Wrapper–Child | RunSpec → capture → LedgerCommand | M1 |
| I03 Project–Query–Evidence | index+pread → views → fetch | M2 (projection), M5 (query) |
| I04 MCP–ControlSurface | stdio porch → nine tools | M1 |

## Integration waves

| Wave | After | Verifies |
|------|-------|----------|
| W1 | M1 | Admit → Execute → Project round-trip; plugin → status frame |
| W2 | M2 | Projection + fetch + auto-promote E2E |
| W3 | M4 | Agent workflow: run + wait + query + fetch |
| W4 | M7 | Full verification battery + CI green |

## Verifier

Freeze HLD + this bulkhead index MUST remain consistent with `trestle-requirements.md` §23 milestones. Any drift is a portfolio gate failure.
