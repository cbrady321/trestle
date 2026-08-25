# Spec — Trestle Console (Porch HTTP service)

> **CANCELLED (2026-08-25).** Porch is OUT. Do not implement `console/porch/`, `/porch/v1`, or `console/web/`.  
> **Phase C complete:** agent MCP playbook shipped — [`docs/agent-console-mcp.md`](../docs/agent-console-mcp.md). See [`plan-daytona-clean-room-scope.md`](plan-daytona-clean-room-scope.md) §0.5.  
> **Future human path:** sessions/telemetry inspection (name TBD) — not porch.

Status: **superseded** — historical reference only.

## Guiding light

### Nearer decisions

- **Parent (SSOT):** `trestle-requirements.md` — `## 1. Goals` — Minimize agent context consumed per unit of work done.
- **Parent (SSOT):** `trestle-requirements.md` — `## 1. Goals` — Extend capability by writing one Python file — no restart, registry edit, or MCP plumbing for new plugins.
- **Parent (SSOT):** `trestle-requirements.md` — `## 1. Goals` — Bounded, backend-independent query interface over run history (nine named views).
- **Parent (SSOT):** `hld/hld-interface-architecture-trestle.md` — `## Goals & Non-Goals` — Nine MCP tools only; request refusals have no `run_id`; only Project emits toward MCP.

### Larger picture

- **Grandparent (SSOT):** `hld/hld-interface-architecture-trestle.md` — `## Goals & Non-Goals` — CLI may be richer than MCP; same verb must not have different admission rules across transports.

### Why we believe that

- Operators should inspect the ledger without stuffing the agent porch. Console is that human porch. It must look and speak like Trestle (handles, named views, outcomes), not like a remote-computer product.

## Goals & Non-Goals

**Goals**

- A stranger agent can implement Console MVP from this file plus the freeze HLD and Trestle Python package — **without any third-party product source tree**.
- Porch HTTP is a **faithful, admission-identical** projection of ControlSurface read/wait/pin/cancel/catalog verbs. It is not a fourth Kernel port and not Execute.
- External names, routes, JSON, Python modules, and file layout are **Trestle-native and deliberately unlike** common sandbox-dashboard APIs — see §3 **Naming wall**.
- Must-slice operator jobs: binding, ledger list, ask/retrieval (Q1), honest not-finalized, refusal UX — not log scroller.

**Non-goals**

- Implementing `run` (admit) from the browser (parked). Invoking plugins stays MCP/CLI.
- Live tail of a still-running run. Kernel HTTP as a freeze amendment.
- Org, billing, runners, snapshots, volumes, in-box filesystem APIs, web terminals, VNC.
- Copying, wrapping, or “extracting” another vendor’s UI, OpenAPI, or SDK.

---

## 1. Clean-room protocol (normative — fail closed)

This is a **clean-room implementation of Trestle contracts**, not an extraction from any other codebase. Spec authors already translated operator jobs into Trestle verbs. Implementers **do not go back to the other tree to see how it was done**.

### 1.1 Chinese wall

| Role | May | Must not |
|------|-----|----------|
| **This spec + freeze HLD** | Define jobs, Lens routes, JSON, UI routes, stack | Cite another vendor’s paths, DTOs, or file names as templates |
| **Implementer agent / human** | Read: this spec, freeze HLD, `trestle/` Python, `trestle-requirements.md` Goals, scope plan §8 if a job ID is unclear | Clone, submodule, vendor, or fetch `daytonaio/daytona`, `nightona-co/nightona`, or any dashboard/API/toolbox source. Must not web-search those trees for “how they built X”. Must not keep a private `vendor/` copy “for reference”. |
| **Reviewer** | Diff Console against **this spec** and freeze envelopes | Accept “it matches the other dashboard” as quality |

If an implementer is stuck on UX, they **extend this spec** (or ask the spec owner). They do not resolve ambiguity by opening the other product.

### 1.2 Similarity test (reject the change)

A change fails clean-room if **any** of these hold:

- File tree or route map is a rename of `apps/dashboard`, `/sandbox`, `/toolbox`, `/playground`, or that product’s OpenAPI tags.
- JSON field names are camelCase clones of that product’s sandbox/runner/snapshot/organization/wallet resources.
- Comments, README, or OpenAPI `info.description` mention Daytona, Nightona, or “ported from”.
- `components/ui` was copied from another repo instead of a **fresh** shadcn/ui init in *this* tree.
- TypeScript client was copied from another `api-client` / `sdk-typescript`; clients must be generated from **`console/openapi.yaml` authored here**.
- UI copy uses sandbox, toolbox, playground, runner, snapshot (OCI), region, organization, workspace, or session-as-connection noun.
- HTTP paths or OpenAPI `operationId` values that mirror another vendor’s sandbox/toolbox/session API (`listSandboxes`, `getToolbox`, `createWorkspace`, …).
- Python functions named `get_sandbox_*`, `fetch_logs`, `list_workspaces`, `ApiClient`, `DashboardService`.
- Wire envelopes `{ "ok", "data" }`, `{ "success", "result" }`, or camelCase top-level resource DTOs on the Porch layer.

Trestle on-disk `console/*` evidence streams are **not** this product. Never name a UI route `/console` meaning stdout. Retrieval uses **ask** / **chunk** / **rows**, not “log viewer.”

### 1.3 License posture

MIT or other permissive dependencies only. Fresh `shadcn` install. No AGPL. Generated code is generated from **our** OpenAPI.

---

## 2. Architecture (Porch is not the Kernel)

```
[Vite + React Console]  --HTTP JSON-->  [Porch (Python, localhost)]  --MCP stdio-->  [trestle serve]
                                              ControlSurface client
                                              NEVER Execute
```

| Piece | Job | Forbidden |
|-------|-----|-----------|
| **Console** | Operator UI. Talks only to Porch. | Importing Kernel, speaking stdio, path fetch, SQL |
| **Porch** | Local HTTP adapter. MCP (or in-process ControlSurface) **client**. Maps §3 routes to the nine tools. | New admission rules, calling Execute, widening verbs, proxying arbitrary host paths |
| **Kernel / `trestle serve`** | Unchanged freeze | HTTP inside Kernel for this unit |

**Why this split:** a Python `console/porch/` package + Vite SPA is a different shape from a NestJS-plus-Next control plane. Porch speaks §3 names; Kernel pictures inside `body` stay freeze-shaped.

**Default bind:** Porch `127.0.0.1:18732` (not 3000/3001). Console dev server elsewhere. CORS: localhost only.

**Packaging:** `console/web/` (or `console/src/`) for UI + `console/porch/` for Python. Not `apps/dashboard`, not `console/lens/`.

---

## 3. Naming wall (HTTP + Python — normative)

### 3.0 Two layers (do not conflate)

| Layer | Who owns names | Examples |
|-------|----------------|----------|
| **Kernel / MCP** (freeze) | HLD — **do not rename in Console work** | `query`, `fetch`, `list_plugins`, `BoundedView`, `run_id` |
| **Porch + Console** (this spec) | **This spec — must be distinct** | `/porch/v1/ledger/…/rows`, `read_ledger_rows()`, `issued`/`body` |

Kernel tool names are Trestle’s own freeze, not a vendor import. The naming wall applies to **everything we add** for Console so the public surface cannot be read as a transcription.

### 3.1 Forbidden vocabulary (Porch wire + UI + Python public API)

sandbox, workspace, toolbox, playground, runner, snapshot (machine image), volume, org, organization, wallet, region, preview-proxy, session (as connection noun), dashboard (as route), terminal (as live stream), ApiClient, getSandbox*, listWorkspaces, toolbox*, mcpInit.

### 3.2 Preferred vocabulary

| Use | Meaning |
|-----|---------|
| run, handle, ledger, view, chunk, registry, callable, retention, join, outcome, porch, binding | Trestle nouns on wire and in UI |
| `admission.*` / `projection.*` | Kernel refusal codes inside `body` |
| `registry_version`, `backend`, `as_of` | Freeze fields inside `body` — pass through |

### 3.3 HTTP surface — `/porch/v1` only

Prefix: **`/porch/v1`**. No `/api/v1`, no `/lens`, no versionless `/v0` from earlier drafts.

**Wire envelope** (all Porch responses — distinct from generic REST):

```json
{ "issued": true, "body": { "...freeze BoundedView | CatalogView | FetchSlice | RunView | RequestOutcome..." } }
```

Refusal (Kernel `RequestOutcome` — still HTTP 200 unless Porch itself is broken):

```json
{ "issued": false, "body": { "code": "projection.not_finalized", "message": "...", "retryable": true, "origin": "projection" } }
```

Porch-local failure (MCP child dead, malformed JSON): HTTP **502** / **400** with `{ "issued": false, "body": { "code": "porch.unbound", "message": "...", "retryable": false, "origin": "porch" } }`. Namespace `porch.*` is Porch-only; never mix with `admission.*` / `projection.*`.

`run_id` **must be absent** on `origin=admission` pictures inside `body`.

| HTTP | OpenAPI `operationId` | Python (`console/porch/`) | Kernel (internal) |
|------|----------------------|----------------------------|-------------------|
| `GET /porch/v1/binding` | `read_binding` | `read_binding()` | `list_plugins` (health) |
| `GET /porch/v1/host_wiring` | `read_host_wiring` | `read_host_wiring()` | static snippet |
| `GET /porch/v1/registry` | `iter_registry` | `iter_registry(cursor=, invalid=)` | `list_plugins` |
| `GET /porch/v1/registry/{name}` | `describe_registry_entry` | `describe_registry_entry(name)` | `describe_plugin` |
| `POST /porch/v1/ledger/{view}/rows` | `read_ledger_rows` | `read_ledger_rows(view, params, cursor)` | `query` |
| `POST /porch/v1/evidence/chunk` | `read_evidence_chunk` | `read_evidence_chunk(handle, window)` | `fetch` |
| `POST /porch/v1/waits/join` | `join_waits` | `join_waits(handles, timeout_ms)` | `await_runs` |
| `POST /porch/v1/waits/stop` | `request_stop` | `request_stop(handle)` | `cancel` |
| `PUT /porch/v1/retention/{handle}` | `pin_retention` | `pin_retention(handle)` | `pin` |
| `DELETE /porch/v1/retention/{handle}` | `unpin_retention` | `unpin_retention(handle)` | `unpin` |

Path `view` ∈ nine freeze ViewNames. Body for rows: `{ "params": {}, "cursor": null }`. Body for chunk: `{ "handle": "<handle>", "window": { "kind": "tail", "count": 50 } }` — use **`handle`**, not `target`, on the Porch wire (Kernel `fetch` still uses `target` inside the MCP client only).

Truncated chunk: `issued: true` with `body.truncated` / `body.scan_bytes` — not `issued: false`.

**No routes** for: computers, sandboxes, toolbox, PTY, org, billing, `POST …/runs` (admit).

Author **`console/openapi.yaml`** from this table (`operationId` = Python names). Generate TS client into `console/web/src/generated/`.

### 3.4 Python package layout (idiomatic, required)

```
console/porch/
  __init__.py          # exports PorchGateway
  gateway.py           # PorchGateway — ASGI app, route wiring
  binding.py           # BindingSpec, read_binding
  registry.py          # iter_registry, describe_registry_entry
  ledger.py            # read_ledger_rows
  evidence.py          # read_evidence_chunk, FetchWindow typing
  waits.py             # join_waits, request_stop
  retention.py         # pin_retention, unpin_retention
  host_wiring.py       # read_host_wiring
  kernel_client.py     # McpKernelClient — **private**; calls MCP tool names; not exported to UI
```

- **Public:** `PorchGateway`, module functions above, `PorchResponse(issued: bool, body: dict)`.
- **Private:** `McpKernelClient.call_query(...)`, `call_fetch(...)` — maps to MCP `query`/`fetch`; never re-exported to OpenAPI or TS.
- **Style:** snake_case, verbs first (`read_*`, `iter_*`, `join_*`, `request_*`), no `*Service`, `*Controller`, `*Resource`.

### 3.5 Why these names (legal + product)

- **Ledger / evidence / registry** — describes what Trestle stores, not where it runs.
- **`read_ledger_rows` not `get_logs`** — retrieval-first; not a log-download API.
- **`binding` not `session`** — avoids session/org-shaped REST from sandbox products.
- **`issued`/`body` not `ok`/`data`** — wire shape is ours; freeze payloads live only inside `body`.
- **Python package `porch`** — matches Trestle “porch” language; not `lens` (HLD internal), not `api` or `dashboard`.

Same admission as MCP: Porch must not accept a query the MCP porch would refuse, or refuse one MCP would accept.

---

## 3bis. Language wall (legacy pointer)

§3 supersedes earlier `/lens/v0` drafts. If any doc still says `console/lens/` or `/lens/v0/session`, treat it as **stale** — use §3.3 only.

---

## 4. UI (operator jobs) — retrieval-first routes

React Router. Forbidden: `/dashboard`, `/playground`, `/sandboxes`, `/sessions`.

| Route | Jobs | Porch calls (`operationId`) | Notes |
|-------|------|----------------------------|--------|
| `/bind` | K1, K2 | `read_binding`, `read_host_wiring` | Not `/session` |
| `/ledger` | R1, R2 | `read_ledger_rows` (`recent_runs`, optional `recent_failures`) | Row → `/ledger/{handle}/ask` |
| `/ledger/{handle}/ask` | R3, R4, Q1, X1 | `read_ledger_rows(run)`, then `read_evidence_chunk` / views as needed | **Ask UI** — show chunk + byte budget + continuation; no log scroller |
| `/registry` | P1 | `iter_registry` | Not `/plugins` if it echoes vendor catalog chrome — **registry** matches Porch |
| `/registry/{name}` | P2 | `describe_registry_entry` | |

**Should:** retrieval chain panel (Q2) on `/ledger/{handle}/ask`; copy host wiring JSON.

**Could:** cancel/stop via `request_stop`; retention via `pin_retention`.

### 4.1 Not-finalized honesty (R-QB-28)

Until durable `evidence_finalized`, only `run` and `recent_runs` succeed. Porch returns `projection.not_finalized` inside `body` for other views. Console shows “not filed yet” — never a blank stream.

After finalize: retrieval UI composes `read_ledger_rows(last_error)` and/or `read_evidence_chunk` with explicit `window` — default `tail` count 50; continue with `range`. No websocket.

### 4.2 Visual stack

- React + Vite + TypeScript
- shadcn/ui **initialized in `console/`** (new) — minimal components only (table, card, button, alert)
- **MVP default:** hand-written TS types against `console/openapi.yaml`
- **Defer (v0.2 ok):** TanStack Query; OpenAPI → generated TS client — see [`console-port-worthiness-verification.md`](console-port-worthiness-verification.md)
- No AGPL components; no xterm attached to a vendor `terminal.url`

---

## 5. Implementer done-when

1. `console/openapi.yaml` describes §3.3 (`/porch/v1`, `operationId` = Python names); TS client generated.
2. Porch binds localhost only, spawns or attaches `trestle serve`, maps §3.3 table, never Execute, never admit/`run`.
3. Console Must routes (§4) work against real Trestle; **ask** UI shows chunks not streams.
4. Similarity + **naming wall** (§1.2, §3): zero forbidden nouns; tree is `console/porch/` + `console/web/`.
5. README: run Porch + UI; paste host wiring JSON. No other-vendor names.

Kernel, nine tools, and freeze envelopes are not in scope to change.

---

## 6. Handoff packet

Paste [`handoff-console-lens-implementer.md`](handoff-console-lens-implementer.md) into a **fresh** agent with **no** prior Daytona/Nightona thread. Give that agent this repo and this spec. Do not give it a checkout of another dashboard.
