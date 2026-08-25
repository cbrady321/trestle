# Agent Handoff — Trestle Console (Porch MVP)

> **CANCELLED (2026-08-25).** Do not implement Porch or web Console.  
> **Active handoff:** write [`docs/agent-console-mcp.md`](../docs/agent-console-mcp.md) per [`plan-daytona-clean-room-scope.md`](plan-daytona-clean-room-scope.md) §0.

**Prerequisite:** ~~worthiness verification~~ — obsolete for Porch track.

## 1. Identity & Framing

You're implementing **Trestle Console**, a local operator web UI over Trestle’s durable execution ledger. Trestle is already shipped (nine MCP tools, spawn-per-run, evidence on disk). Console is a **new product layer**, not Trestle’s on-disk `console/*` streams.

Your role is **clean-room implementer**: build **Porch** (`console/porch/`) + Vite/React UI from the spec. Use §3 HTTP and Python names exactly. You are **not** extracting, porting, or transcribing another vendor’s dashboard, API, or SDK.

## 1a. Goals (cascade)

Cite `helper-goal-cascade`. Refresh from the named files, not from this prompt’s wording if they drift.

**Parent (SSOT):** `hld/plan-daytona-clean-room-scope.md` — `## 0. Kickoff — implement now`

- Execute §0.5 build order; §0.9 done-when checklist.
- Retrieval-first: bounded answer card (Q1), not log scroller.

**Parent (SSOT):** `hld/spec-console-lens-mvp.md` — `## Goals & Non-Goals`

- Porch HTTP: admission-identical to MCP; §3 naming wall (`/porch/v1`, `read_*`, `issued`/`body`).
- No third-party source tree; no log scroller MVP.

**Parent (SSOT):** `trestle-requirements.md` — `## 1. Goals`

- Minimize agent context consumed per unit of work done.
- Extend capability by writing one Python file — no restart, registry edit, or MCP plumbing for new plugins.
- Bounded, backend-independent query interface over run history (nine named views).

**Parent (SSOT):** `hld/hld-interface-architecture-trestle.md` — `## Goals & Non-Goals`

- Nine MCP tools only; request refusals have no `run_id`; only Project emits toward MCP.

**This-node:** Implement per `hld/spec-console-lens-mvp.md` §2–§5: `console/porch/` with §3.3–§3.4 names, `console/openapi.yaml`, generated client, retrieval-first UI (§4). Pass similarity + naming wall.

**Larger picture:** `hld/hld-interface-architecture-trestle.md` — `## Goals & Non-Goals` — CLI may be richer than MCP; same verb must not have different admission rules across transports.

**Do not:** refresh these bullets from a previous chat; treat this prompt as SSOT; “look up how the other dashboard did it.”

## 2. Current State — what's true right now

**What exists**

- Trestle kernel in `trestle/`, v0.1, pytest green locally (102 tests). `trestle serve` is stdio MCP only.
- Freeze: `hld/hld-interface-architecture-trestle.md` (nine tools, envelopes, ViewRow table, fetch windows).
- Scope + kickoff: `hld/plan-daytona-clean-room-scope.md` §0 (build order).
- **Implementer SSOT:** `hld/spec-console-lens-mvp.md` — **no Console code yet.**
- Program `programs/trestle-v01` is complete; do not reopen kernel architecture.

**What's decided (and why)**

- Console talks to **Porch** (`console/porch/`), not Kernel HTTP.
- Porch is MCP **client** of `trestle serve`; must not call Execute.
- No `POST` admit/`run` from the UI in this hop (playground parked).
- No live tail (Kernel refuses evidence views until `evidence_finalized`).
- Stack: Python Porch + Vite/React + fresh shadcn + TanStack Query. OpenAPI authored here; client generated; MIT/permissive only.
- External API is **`/porch/v1`** with Python `read_*` names — not `/lens`, not sandbox REST.

**What's been tried and killed**

- Vendoring or carving another AGPL dashboard/monorepo — copyleft + corporate embedding risk; also the wrong product (runners, billing, toolbox).
- Treating Trestle `console/*` as this UI.
- Cloning another vendor’s OpenAPI/SDK and “rebranding.”

**Where the numbers stand**

- Console code: zero.
- Spec + this handoff: on disk. Similarity test is the legal/quality gate, not “looks familiar.”

## 3. Open Threads

- Lens spawn vs attach to an already-running `trestle serve` — pick one default; document the other. Do not block.
- In-process ControlSurface vs MCP stdio — MCP stdio is the default in the spec (process isolation from Kernel). In-process only if you stay a client of ControlSurface and still never Execute.
- Should jobs (catalog pages, last_error, events, provenance, agent-porch copy): implement after Must if time; **do not** skip Must for them.
- Trestle CI `master` trigger / root README / `v0.1.0` tag — **out of this hop** unless you finish Must early.

**Blocked:** nothing on Trestle for the Must slice except a working local `trestle serve`.

## 4. Constraints & Non-Negotiables

**Clean-room (fail closed)** — spec §1 is law:

- Do **not** clone, fetch, submodule, or read source from `daytonaio/daytona` or `nightona-co/nightona` (any tag). Do **not** web-search those codebases or dashboards for implementation.
- Do **not** create `vendor/` of those trees. Do **not** copy `components/ui`, API clients, or page TSX from them.
- If UX is ambiguous, patch `hld/spec-console-lens-mvp.md` (or stop and ask). Do not resolve by inspecting the other product.
- Comments and READMEs: **zero** mentions of those product names.
- Pass spec §1.2 similarity test.

**Kernel freeze**

- Nine tools only. No tenth. Plugin schemas never on `tools/list` or catalog as a type dump.
- `RequestOutcome` admission pictures have **no** `run_id`. Never render `rejected` as a run row.
- Fetch never accepts filesystem paths. Scan-budget overflow is truncated success, not `ok: false`.
- Porch admission **identical** to MCP for the verbs you expose.

**Jargon**

- Trestle `console/*` = evidence pipe bytes. **Console** = web UI. **Porch** = HTTP adapter (`console/porch/`).

## 5. Immediate Next Steps

Follow **`hld/plan-daytona-clean-room-scope.md` §0.5** in order:

1. `console/openapi.yaml` (spec §3.3)
2. `console/porch/` skeleton + `read_binding` + spawn `trestle serve`
3. `read_ledger_rows`, `read_evidence_chunk`
4. `console/web/` + `/bind`, `/ledger`, `/ledger/{handle}/ask`
5. README + forbidden-noun grep pass

**Out of scope this hop:** Trestle CI, `v0.1.0` tag, root README.

## 6. Questions Worth Sitting With

- Would a reviewer who knows sandbox dashboards see **handles and named views**, or renamed computers? If the latter, you failed the API goal.
- Can an operator diagnose a failed run **without** the agent spending a turn? If not, G1 missed.
- Does any button imply live logs while the Kernel would return `projection.not_finalized`? That is a product lie.

## 7. Reference Materials

**Read (only these for behavior):**

- `hld/spec-console-lens-mvp.md` — implementer SSOT
- `hld/hld-interface-architecture-trestle.md` — nine tools, ViewRow freeze, FetchWindow, Dual-error, ControlSurface
- `hld/plan-daytona-clean-room-scope.md` §0 — **start here** (kickoff, build order, done-when)
- `trestle-requirements.md` — `## 1. Goals`
- `trestle/` — how to spawn `trestle serve`; envelope shapes in code if HLD and code disagree, **HLD wins**; file a note, do not “fix” freeze in this hop

**Do not read for implementation:** any Daytona/Nightona source, dashboard, toolbox OpenAPI, or SDK tree.

**Local verify:** `pip install -e ".[dev]"`; `trestle serve` (or let Porch spawn it); plan §0.7 curl smoke tests.
