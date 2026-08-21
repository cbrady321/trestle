# Target Delta — trestle-v01

**Entry mode:** brownfield_migration  
**Base ref:** `design-frozen` (requirements v0.7, freeze HLD, spikes done, premature scaffolding only)  
**Target ref:** `v0.1-complete` (milestones M1–M7, §19 verification, CI green)

## Current state (base)

| Area | Status |
|------|--------|
| Requirements | Draft v0.7; §25 closed; normative MUST/MAY complete |
| Freeze HLD | `hld/hld-interface-architecture-trestle.md` — three ports, nine tools, envelope families |
| TTY overlay | Drafted; additive only; not an M1–M7 gate |
| Spikes | M0, M0.5, M0.6 **done** — spawn-per-run, projection via index+pread, stdio FastMCP 3.4.7 |
| Product code | Premature scaffolding: `trestle/common/*`, `trestle/child/{serialize,index}.py`, `trestle/cli.py` (imports missing `trestle.server.*`) |
| Tests | **None** — CI expects `tests/` |
| CI | `.github/workflows/ci.yml` configured; will fail until tests land |
| Root ledger | Stale `.ledger.yaml` at repo root — **superseded** by this program |

## Target state (capstone)

Per §23 milestones M1–M7 and §19 verification oracles (R-VER-1 through R-VER-12 where applicable).

### M1 — Skeleton

FastMCP stdio porch; Admit/Execute/Project skeleton; ndjson ledger; snapshot registry; spawn-per-run wrapper+child; PluginSurface + automatic runtime; poll capture; status frame; epoch; drain.

**Exit:** Runnable `run` → terminal RunView for a trivial plugin.

### M2 — Bounding

`result.index` projection; capture limits; artifacts + pins; fetch; auto-promote `work/outputs/`.

**Exit:** R-VER-2 hostile-plugin responsiveness.

### M3 — Durability

Cancel/timeout; atomic writes; recovery (ledger authority).

**Exit:** §5.6 + second-pass recovery.

### M4 — Waiting

`wait_ms`, `await_runs` on stdio.

**Exit:** Long wait without Tasks mapper.

### M5 — Query

Filesystem backend; nine views; paging; `backend`/`as_of`; R-QB-28 running-run restriction.

**Exit:** Query conformance (R-VER-10).

### M6 — Extending

Hot reload; `registry_version`; promotion budget.

**Exit:** Plugin drop-in without server edit.

### M7 — Operations

Retention; CLI doctor/recover; chaos matrix; workflow benchmarks.

**Exit:** R-VER-3, R-VER-4–5.

## Gap summary

| Gap | Units |
|-----|-------|
| No server/, wrapper/, plugin/ modules | M1 |
| No tests/ directory | M1 (creates) |
| No projection boundary | M2 |
| No recovery | M3 |
| No wait model | M4 |
| No query backend | M5 |
| No hot reload | M6 |
| No operator CLI / chaos / retention | M7 |

## Brownfield disposition

- Extend or replace `trestle/common/*` and `trestle/child/*` behind freeze HLD contracts.
- Fix `trestle/cli.py` dead imports when `trestle/server/` lands (M1 or M7).
- Do not treat existing scaffolding as ground truth.

## Explicit non-goals (this program)

- TTY plugin shipping (advertise-and-refuse OK)
- M5.1 SQLite
- FastMCP Tasks / Docket / Redis
- Warm-fork supervisors
- v0.2 composition (§20)

## Verification closure

Program complete when: M1–M7 demonstrable; integration waves pass; CI green; US-01–24 core stories pass; hostile-plugin (R-VER-2), chaos (R-VER-3), workflow benchmarks (R-VER-4–5) satisfied.
