# Spec — Operator sessions & telemetry

Status: **complete** — E5/E5b + v0.2 @ operator API 0.2.0.  
Date: 2026-08-25  
Replaces: [`spec-console-lens-mvp.md`](spec-console-lens-mvp.md) (cancelled porch track).

## Goals & Non-Goals

**Goals**

- Stranger implementer builds operator HTTP + minimal web UI from this spec + `trestle/` — no vendor source trees.
- HTTP is an **in-process ControlSurface projection** — not MCP stdio bridge, not Execute, not admit/`run` from UI.
- Retrieval-first: sessions list → ask page with bounded telemetry chunks — not log scroller.

**Non-goals**

- Replacing agent stdio MCP; ninth tool; live tail; porch naming; AGPL UI deps.

## Architecture

```text
[Vite + React UI]  --HTTP JSON-->  [Operator API Starlette]  --in-process-->  ControlSurface
       console/web/                    trestle/ops/                    trestle/server/
```

| Piece | Job | Forbidden |
|-------|-----|-----------|
| **Web UI** | Operator browser app; talks only to `/ops/v1` | Kernel imports, stdio MCP, SQL, path fetch |
| **Operator API** | `trestle ops serve`; maps §3 routes to ControlSurface | Execute, new admission rules, `run` from HTTP |
| **MCP porch** | `trestle serve` — agent path | Changed by this unit |

**Default bind:** `127.0.0.1:18733`. CORS: localhost origins only.

**Packaging:** `trestle/ops/` (Python ASGI) + `console/web/` (UI) + `console/openapi.yaml`.

## Naming wall

### Forbidden (wire + UI + public Python)

`porch`, `/porch/v1`, `console/porch/`, sandbox, workspace, toolbox, playground, runner, snapshot (OCI), dashboard (route), session-as-connection, `ApiClient`, `getSandbox*`, `fetch_logs`.

### Preferred

| Use | Meaning |
|-----|---------|
| session, handle, telemetry, chunk, registry, view, outcome | Trestle operator nouns |
| `admission.*` / `projection.*` | Kernel codes inside `body` |
| `issued` / `body` | Operator wire envelope |

Kernel MCP tool names (`query`, `fetch`, …) are freeze — do not rename on the Kernel side.

## HTTP surface — `/ops/v1`

**Envelope** (all operator responses):

```json
{ "issued": true, "body": { "...BoundedView | CatalogView | FetchSlice | RunView..." } }
```

Kernel `RequestOutcome`:

```json
{ "issued": false, "body": { "code": "projection.not_finalized", "message": "...", "retryable": true, "origin": "projection" } }
```

Operator-local failure (HTTP 502): `{ "issued": false, "body": { "code": "operator.unreachable", "origin": "operator", ... } }`.

`run_id` **absent** when `body.origin` is `admission`.

| HTTP | `operationId` | Python (`trestle/ops/`) | ControlSurface |
|------|---------------|---------------------------|----------------|
| `GET /ops/v1/health` | `read_health` | `read_health()` | `list_plugins` |
| `GET /ops/v1/host_wiring` | `read_host_wiring` | `read_host_wiring()` | static snippet |
| `GET /ops/v1/registry` | `iter_registry` | `iter_registry()` | `list_plugins` |
| `GET /ops/v1/registry/{plugin_id}` | `describe_registry_entry` | `describe_registry_entry(id)` | `describe_plugin` |
| `POST /ops/v1/sessions/{view}/rows` | `read_session_rows` | `read_session_rows(view, params, cursor)` | `query` |
| `POST /ops/v1/telemetry/chunk` | `read_telemetry_chunk` | `read_telemetry_chunk(handle, window)` | `fetch` |
| `POST /ops/v1/actions/cancel` | `cancel_run` | `cancel_run(handle)` | `cancel` |
| `PUT /ops/v1/retention/{handle}` | `pin_retention` | `pin_retention(handle)` | `pin` |
| `DELETE /ops/v1/retention/{handle}` | `unpin_retention` | `unpin_retention(handle)` | `unpin` |
| `POST /ops/v1/waits/join` | `join_waits` | `join_waits(handles, mode, timeout_ms)` | `await_runs` |

`view` ∈ nine freeze ViewNames. Rows body: `{ "params": {}, "cursor": null }`. Chunk body: `{ "handle": "<handle>", "window": { "kind": "tail", "count": 50 } }` — **`handle`** on operator wire (maps to Kernel `fetch` `target` internally).

**No routes** for: admit/run, live stream, SQL, filesystem paths.

Author `console/openapi.yaml` from this table.

## Python layout

```text
trestle/ops/
  __init__.py       # exports create_app
  gateway.py        # Starlette routes, envelope helpers
  health.py         # read_health
  sessions.py       # read_session_rows
  telemetry.py      # read_telemetry_chunk
  registry.py       # iter_registry, describe_registry_entry
  actions.py        # cancel_run, pin_retention, unpin_retention, join_waits
  host_wiring.py    # read_host_wiring
  serve.py          # uvicorn entry for CLI
```

- **Public:** `create_app(kernel)`, module functions above.
- **Private:** envelope helpers; no re-export of ControlSurface to OpenAPI.

CLI: `trestle ops serve [--host 127.0.0.1] [--port 18733] [--home PATH]`.

## UI routes (retrieval-first)

| Route | Jobs | Operator calls | Notes |
|-------|------|----------------|-------|
| `/` | H1 | `read_health` | Connection / registry_version |
| `/host-wiring` | — | `read_host_wiring` | MCP stdio snippet copy |
| `/sessions` | S1 | `read_session_rows(recent_runs \| recent_failures)` | Tab toggle |
| `/sessions/{handle}/ask` | S2, T1, X1 | `read_session_rows(run)` + chunk + actions | Ask UI + retrieval chain |
| `/registry` | — | `iter_registry` | Plugin catalog |
| `/registry/{name}` | — | `describe_registry_entry` | Plugin detail |

**Not-finalized (R-QB-28):** until `evidence_finalized`, only `run` and `recent_runs` views succeed; UI shows “telemetry not filed yet.”

**Stack:** React + Vite + TS; fresh shadcn (table, card, button, alert); hand-written types from `console/openapi.yaml`.

## Implementer done-when (E5)

1. `console/openapi.yaml` describes §3 table.
2. `trestle ops serve` binds localhost; maps routes; never Execute; never HTTP `run`.
3. Must UI routes (§4) work against real ledger; ask UI shows chunks not streams.
4. Naming wall: zero forbidden nouns in tree and wire.
5. `pytest` includes operator API tests (`TestClient`).
6. README section: operator HTTP quick start.

Kernel freeze envelopes and nine MCP tools are **out of scope** to change.

## Handoff

Implement from this spec only. Agent playbook: [`docs/agent-console-mcp.md`](../docs/agent-console-mcp.md). Operator playbook: [`docs/operator-sessions-telemetry.md`](../docs/operator-sessions-telemetry.md).
