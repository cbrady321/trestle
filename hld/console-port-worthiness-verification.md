# Console port worthiness verification

> **OBSOLETE (2026-08-25).** Porch/web **Proceed to implement** verdicts are void. Owner: Porch OUT; human inspection = future **sessions/telemetry** (not porch).  
> **Phase C complete:** [`plan-daytona-clean-room-scope.md`](plan-daytona-clean-room-scope.md) §0 — agent MCP playbook shipped.

Status: **C0 complete (historical)** — gate for cancelled Porch implement track.

## Guiding light

Bound parent goals (cite path + heading):

- `trestle-requirements.md` — `## 1. Goals` — **G1** minimize agent context consumed per unit of work done; **G3** extend capability by writing one Python file; **G4** retain all framework-observable evidence, surface almost none; **G7** bounded, backend-independent query interface over run history (nine named views).
- `hld/hld-interface-architecture-trestle.md` — `## Goals & Non-Goals` — nine MCP tools only; `RequestOutcome` refusals have no `run_id`; only Project emits toward MCP; CLI may be richer than MCP but same verb must not have different admission rules across transports.
- `hld/plan-daytona-clean-room-scope.md` — `## Strategic correction — retrieval, not log viewing` — Console proves bounded retrieval for humans (focused question → small answer + continuation handle), not a log scroller; kernel + CLI may already satisfy the core agent job; Console is an optional proving ground.

## Verdict summary (void — Porch cancelled)

- **Proceed to implement:** **None** — entire Porch/web list below is **cancelled**. Phase C agent MCP docs complete — see plan §0.5.

- ~~**Proceed to implement:**~~ (historical)
  - `console/openapi.yaml` (Porch contract SSOT; steps 1–8 in plan §0.5)
  - `console/porch/` — `PorchGateway`, `kernel_client.py`, Starlette/FastAPI ASGI, MCP stdio spawn/attach to `trestle serve`
  - `GET /porch/v1/binding` (`read_binding`)
  - `POST /porch/v1/ledger/{view}/rows` (`read_ledger_rows`) — at minimum `recent_runs` and `run`
  - `POST /porch/v1/evidence/chunk` (`read_evidence_chunk`)
  - UI routes: `/bind` (minimal connection status), `/ledger`, `/ledger/{handle}/ask` with **bounded answer card** (chunk + byte budget + continuation handle)
  - Cross-cutting: admission-identical refusals (X1), `projection.not_finalized` honesty (R4), no `run_id` on `admission.*`
  - `console/web/` — Vite + React + TypeScript; minimal shadcn init for layout/forms only
  - Thin hand-written TS types against `openapi.yaml` (codegen optional later)

- **Defer:**
  - `GET /porch/v1/host_wiring` (K2) — README + copy-paste snippet sufficient for MVP wiring
  - `/registry`, `iter_registry`, `describe_registry_entry` (P1–P2) — G3 catalog UX; v0.2
  - `recent_failures` tab on `/ledger` (R2)
  - Retrieval chain panel (Q2), default `last_error` preset on ask (D1)
  - TanStack Query — three routes do not need a cache library for MVP
  - OpenAPI → generated TS client — keep spec authoritative; generate in v0.2 or when route count grows
  - `join_waits`, `pin`/`unpin`, `cancel` endpoints (R5, M1–M2) — operator power tools, not retrieval proof

- **Drop (confirmed dead for MVP):**
  - Log scroller / live tail (L1) — contradicts G1/G4/G7 and R-QB-28
  - Artifact file browser as MVP centerpiece (A1–A2) — path-shaped UX; use bounded `fetch` on ask page only if needed
  - `POST …/runs` / admit from UI (X2) — freeze; playground parked
  - Sandbox/runner/toolbox/snapshot/org patterns — compute plane; already dropped in plan

- **Console overall:** **Build subset** — Porch + minimal retrieval-first web (bind status, run list, ask page). **Not** a port of another product: Daytona study closed with almost nothing worth extracting; public interface docs informed porch *pattern* only. Do **not** build a dashboard-shaped product; if implementation drifts toward catalog chrome, wiring wizards, or scroll panes, stop and cut scope.

### Console overall stress test

If we never built a web UI, Trestle still meets **G1 for agents** — MCP `query` + `fetch` + DefaultAgentSuccess is the primary optimization target and is shipped. Operators already have a richer **CLI** OperatorContract (`doctor`, query views, fetch windows) on the same ControlSurface verbs without burning agent context. Console does **not** reduce agent tokens directly; it reduces **human→agent mediation** when an operator would otherwise paste ledger dumps into a chat to get diagnosis. That value is real but narrow: it must be measured as “operator gets a bounded answer without opening a log file or asking an agent to re-fetch,” not as “prettier MCP.” The **minimum** Console that earns maintenance is: Porch (stdio bridge) + three UI routes where `/ledger/{handle}/ask` composes `read_ledger_rows` + `read_evidence_chunk` with explicit windows and honest not-finalized — same grammar an agent should use. Anything beyond that (registry browser, host wiring editor, retrieval analytics panel) is product polish, not goal proof.

## Component table

| Component | Verdict | G1 | G3 | G4 | G7 | Freeze | Rationale |
|-----------|---------|----|----|----|----|--------|-----------|
| `console/porch/` (Python package) | **Keep** | Indirect | — | — | Enables | ✓ MCP client only | Browsers cannot speak stdio MCP. Porch is transport infrastructure for any human porch; it does not thicken `tools/list`. Without it, only CLI/MCP remain — valid, but we would not prove the HTTP porch pattern. |
| `PorchGateway` + `kernel_client.py` | **Keep** | Indirect | — | — | Enables | ✓ nine tools, no Execute | Same as above. Private MCP mapping is the only lawful way to expose ControlSurface reads to HTTP without a Kernel HTTP port amendment. |
| Starlette / FastAPI (Porch ASGI) | **Keep** | — | — | — | — | ✓ | Minimal Python HTTP stack; no goal impact but required to serve `/porch/v1`. Replace only if spec changes. |
| `console/openapi.yaml` | **Keep** | — | — | — | Contract | ✓ admission identity | Machine-readable Porch contract; prevents hand-copied SDK drift and supports future codegen. Not agent-facing. |
| `GET /porch/v1/binding` | **Keep** | Low | — | — | — | ✓ health via `list_plugins` | Operators and UI need a single “is Trestle reachable?” probe. Duplicates `trestle doctor` for CLI users but is necessary for browser-first bind flow. |
| `GET /porch/v1/host_wiring` | **Defer** | Low | — | — | — | ✓ static snippet | Helps MCP host setup (less retry churn) but does not exercise retrieval grammar. README + one JSON block achieves most of G1 wiring value for MVP. |
| `POST /porch/v1/ledger/{view}/rows` | **Keep** | **Yes** | — | **Yes** | **Yes** | ✓ `query` only | Core human projection of nine named views. Lets operators pick runs and inspect status without agent turns. Must not become SQL/path export. |
| `POST /porch/v1/evidence/chunk` | **Keep** | **Yes** | — | **Yes** | **Yes** | ✓ `fetch` windows | Core bounded retrieval. Answers focused questions with slice + budget + continuation — same as agents, off-model for humans. |
| `join_waits`, `request_stop`, `pin`/`unpin` routes | **Drop** (MVP) | Neutral | — | — | — | ✓ if added later | Operator actions, not retrieval proof. CLI/MCP already expose them; UI buttons risk dashboard creep. Revisit v0.2 if operator UX demands. |
| `/bind` UI | **Keep** | Low | — | — | — | ✓ | Minimal connection/status surface for K1. Can be a single panel (binding health + “Porch connected to …”) — not a settings app. |
| `/ledger` list UI | **Keep** | **Yes** | — | Low | **Yes** | ✓ `recent_runs` | Navigation chrome only. Lists handles/status; row click → ask. No tail columns, no inline log preview. |
| `/ledger/{handle}/ask` UI (Q1) | **Keep** | **Yes** | — | **Yes** | **Yes** | ✓ compose query+fetch | **North star.** Proves retrieval grammar visually: bounded chunk card, byte budget, continuation handle, optional composed `last_error` row — not a stream. |
| `/registry` + registry endpoints | **Defer** | Low | **Yes** | — | — | ✓ catalog verbs | G3 visibility for humans is valuable but not required to prove G7 retrieval. Agents pull `describe_plugin`; dumping catalog into UI does not reduce agent context. v0.2. |
| Log scroller / artifact browser (L1, A1–A2) | **Drop** | **Anti** | — | **Anti** | **Anti** | ✗ live tail | Duplicates unbounded evidence exposure; killed by strategic correction and R-QB-28. Replacement: bounded chunk on ask page. |
| Admit / `run` from UI (X2) | **Drop** | Neutral | — | — | — | ✗ no Execute | Violates freeze and clean-room scope. MCP/CLI only for admit. |
| Refusal UX — no `run_id` on `admission.*` (X1) | **Keep** | — | — | **Yes** | — | ✓ | Cross-cutting honesty requirement on every Porch route and UI surface. |
| `projection.not_finalized` UX (R4) | **Keep** | **Yes** | — | **Yes** | **Yes** | ✓ R-QB-28 | Ask page must show “not filed yet,” never fake streams. Behavior, not a separate route. |
| `recent_failures` view on `/ledger` (R2) | **Defer** | Low | — | — | **Yes** | ✓ | Same endpoint as R1 with different view param; nice tab, not retrieval proof. |
| Retrieval chain panel (Q2) | **Defer** | Low | — | Low | — | ✓ | Educational bytes-fetched UI; does not reduce agent cost. Add after ask page works. |
| Default `last_error` preset (D1) | **Defer** | **Yes** | — | **Yes** | **Yes** | ✓ | Good ask-page default but composable from existing endpoints; ship ask shell first. |
| **Vite + React + TypeScript** | **Keep** | — | — | — | — | ✓ | Pragmatic SPA shell for three routes. Lighter static HTML could work but would not match spec handoff or component reuse; acceptable maintenance cost for proving human UX once. |
| **shadcn/ui** (fresh init) | **Should** → **Keep (minimal)** | — | — | — | — | ✓ MIT | Not goal-essential — bare CSS would suffice — but fresh MIT init satisfies similarity test and speeds forms/tables without AGPL. Restrict to table, card, button, alert. |
| **TanStack Query** | **Defer** | — | — | — | — | ✓ | Three pages with explicit fetch on navigation do not need cache orchestration. Thin `fetch` + component state is enough for MVP; avoids dep weight. |
| **OpenAPI → TS client codegen** | **Defer** | — | — | — | — | ✓ | `openapi.yaml` remains SSOT. ~5 operations are faster to type by hand than to wire codegen in bootstrap. Generate when surface grows. |

**Legend:** G1/G4/G7 columns — **Yes** = directly serves goal; **Low** = marginal; **Anti** = contradicts goal; **Indirect/Enables** = infrastructure; **—** = not applicable.

## Recommended plan/spec edits

- `hld/plan-daytona-clean-room-scope.md` §0.5 step **6** — change “TanStack Query, generated client” to **optional**: hand-written TS types default; TanStack Query and codegen deferred per this doc.
- `hld/plan-daytona-clean-room-scope.md` §0.5 step **7** — Must UI routes = `/bind`, `/ledger`, `/ledger/{handle}/ask` only; `/registry` stays in “Should after Must.”
- `hld/plan-daytona-clean-room-scope.md` §0.6 Connect row — `read_host_wiring` moved to **Defer** column; binding health only for MVP Connect job.
- `hld/plan-daytona-clean-room-scope.md` §0.9 done-when — add: “No TanStack Query required for MVP pass.”
- `hld/spec-console-lens-mvp.md` §4.2 — note TanStack Query and OpenAPI codegen as **Should/defer** for MVP; hand-written client acceptable.
- `hld/handoff-console-lens-implementer.md` — implementer Must list already gated on this doc; no edit required beyond reading **Proceed to implement** above.

## Implementer unblocks when

- [x] This file exists with verdict on every inventory row from `handoff-console-goals-verification.md` §4.
- [x] **Proceed to implement** list is minimal and retrieval-first (no log scroller, no registry MVP, no operator power tools).
- [x] **Console overall** recommendation is explicit (**Build subset**).
- [x] Plan §0.5–§0.6 patched where verdict ≠ kickoff (see Recommended plan/spec edits).
- [ ] Human sign-off (product owner) on **Build subset** vs **Defer entire initiative** — engineering default is Build subset; escalate if owner prefers CLI-only.

After the plan patch and optional sign-off, start Phase **C1** with [`handoff-console-lens-implementer.md`](handoff-console-lens-implementer.md). Implement **only** items under **Proceed to implement** above.
