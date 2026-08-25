# Requirements — Operator sessions & telemetry (Phase D / E4)

Status: **complete** — E5 API + E5b web shipped @ `0c2ed67`.  
Date: 2026-08-25  
Supersedes: porch-named human surfaces (`spec-console-lens-mvp.md` — cancelled).

## Guiding light

### Parent (SSOT)

- `trestle-requirements.md` — `## 1. Goals` — **G1** minimize agent context; **G4** retain evidence, surface almost none; **G7** nine named views + bounded fetch.
- `hld/hld-interface-architecture-trestle.md` — nine MCP tools for agents; same verb must not have different admission rules across transports.
- `trestle-requirements.md` — **R-OPS-10** optional HTTP operator API; **R-WAIT-14** non-blocking `run` for agents.

### This-node goal

Humans inspect **sessions** (runs) and **telemetry** (bounded evidence slices) through a retrieval-first operator surface — not a log dashboard, not porch naming, not agent MCP stdio.

### Vocabulary

| Term | Meaning |
|------|---------|
| **Session** | A Trestle **run** (`run_id` / handle). Not a connection noun, not a vendor workspace. |
| **Telemetry** | Bounded projection bytes: view rows, fetch chunks, status frames — never raw filesystem paths. |
| **Operator API** | Trestle-owned HTTP app (`/ops/v1`) projecting ControlSurface read verbs in-process. |
| **Agent porch** | `trestle serve` stdio MCP — unchanged; not replaced by operator HTTP. |

## Functional requirements

### R-OP-1 Transport separation

- Agent path **MUST** remain stdio MCP (`trestle serve`, R-FMC-1).
- Operator HTTP **MUST** be a separate command (`trestle ops serve`) and bind **localhost only** (default `127.0.0.1:18733`).
- Operator HTTP **MUST NOT** use FastMCP `http_app()` (R-FMC-4).

### R-OP-2 Admission identity

- Operator `query` / `fetch` **MUST** call the same `ControlSurface.query` / `ControlSurface.fetch` as MCP — identical refusal codes, identical `projection.not_finalized` behavior (R-QB-28).
- Operator HTTP **MUST NOT** add SQL, filesystem paths, or live tail endpoints.

### R-OP-3 MVP operator jobs (Must)

| Job ID | Operator need | HTTP | ControlSurface |
|--------|---------------|------|----------------|
| **H1** | Is Trestle reachable? | `GET /ops/v1/health` | `list_plugins` (registry_version) |
| **S1** | List recent sessions | `POST /ops/v1/sessions/recent_runs/rows` | `query(recent_runs)` |
| **S2** | Session status row | `POST /ops/v1/sessions/run/rows` | `query(run, {run_id})` |
| **T1** | Bounded telemetry chunk | `POST /ops/v1/telemetry/chunk` | `fetch(handle, window)` |
| **X1** | Honest refusals | all routes | no `run_id` on `admission.*`; `issued: false` on `RequestOutcome` |

### R-OP-4 Ask UI (Must for web MVP)

- Route `/sessions/{handle}/ask` composes session row + telemetry chunk(s) with explicit windows — **bounded answer card** (bytes + continuation handle), not a log scroller.
- **MUST** show `projection.not_finalized` as “telemetry not filed yet” — never fake streams.

### R-OP-5 Deferred (v0.2)

- Registry browser, host wiring editor, `recent_failures` tab, retrieval chain analytics, join/cancel/pin from UI.
- Admit / `run` from browser (Drop — freeze).
- Live tail of running sessions (Drop — R-QB-28).

### R-OP-6 Banned naming

`porch`, `Porch`, `/porch/v1`, `console/porch/`, sandbox, workspace, toolbox, playground, runner (compute), dashboard-as-route.

## Non-functional

- **N-OP-1** Wire envelope: `{ "issued": bool, "body": { ...freeze envelope... } }` — operator-local failures use `origin: "operator"`, code prefix `operator.*`.
- **N-OP-2** OpenAPI SSOT at `console/openapi.yaml` — operationIds match Python public functions.
- **N-OP-3** UI stack: Vite + React + TypeScript; fresh MIT shadcn init; hand-written TS types for MVP.

## Done-when (E4)

- [x] This requirements file exists with Must jobs H1, S1, S2, T1, X1, ask UI R-OP-4.
- [x] [`spec-operator-sessions-telemetry.md`](spec-operator-sessions-telemetry.md) implements routes, layout, and naming wall.
- [x] E5 operator API MVP: `trestle ops serve`, tests green.
- [x] E5b web UI: `console/web/` routes per spec §4.

## References

- [`spec-operator-sessions-telemetry.md`](spec-operator-sessions-telemetry.md) — implementation spec
- [`plan-daytona-clean-room-scope.md`](plan-daytona-clean-room-scope.md) — §11 Phase E
- [`docs/agent-console-mcp.md`](../docs/agent-console-mcp.md) — agent SSOT (orthogonal)
