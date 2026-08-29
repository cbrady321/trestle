# Operator sessions & telemetry playbook

**Audience:** humans inspecting run history and bounded telemetry — not coding agents.  
**SSOT for:** `trestle ops serve`, `/ops/v1` routes, `console/web/` UI.  
**Spec:** [`hld/spec-operator-sessions-telemetry.md`](../hld/spec-operator-sessions-telemetry.md).  
**Agent path (separate):** [`docs/agents.md`](agents.md) (quick start) · [`docs/agent-console-mcp.md`](agent-console-mcp.md) (full playbook) — stdio MCP only.

Operators use **`trestle ops serve`** for read-only sessions and telemetry. Agents continue to use **`trestle serve`** (nine MCP tools). The operator surface does **not** admit or `run` work from the browser.

---

## 0. Bootstrap

### Install

```bash
pip install -e ".[dev]"
mkdir -p ~/.trestle/plugins
cp examples/plugins/echo.py ~/.trestle/plugins/
trestle doctor
```

### Build UI (first time or after web changes)

```bash
cd console/web && npm install && npm run build
```

`trestle ops serve` serves `console/web/dist` when present. Without a build, the API still works — only the browser UI is missing.

### Start operator server

```bash
trestle ops serve              # http://127.0.0.1:18733
trestle ops serve --port 18734 # alternate port
```

**Dev UI** (hot reload, proxies `/ops` to ops serve):

```bash
# terminal 1
trestle ops serve

# terminal 2
cd console/web && npm run dev   # http://127.0.0.1:5173
```

### Populate sessions

The UI lists runs from the local ledger. If **Recent sessions** is empty, create a run first:

```bash
python scripts/smoke_agent_mcp.py    # scripted echo run
# or attach an MCP host and call run(echo) via trestle serve
```

---

## 1. Golden workflow

| Step | Surface | Purpose |
|------|---------|---------|
| 1 | `GET /ops/v1/health` or `/` | Confirm reachability and `registry_version` |
| 2 | `/sessions` | List `recent_runs` rows |
| 3 | `/sessions/{run_id}/ask` | Session status + bounded telemetry chunks |
| 4 | Ask buttons | `fetch` result jsonpath, `query` last_error |

**Example API sequence:**

```bash
curl -s http://127.0.0.1:18733/ops/v1/health

curl -s -X POST http://127.0.0.1:18733/ops/v1/sessions/recent_runs/rows \
  -H 'Content-Type: application/json' \
  -d '{"params": {}}'

curl -s -X POST http://127.0.0.1:18733/ops/v1/telemetry/chunk \
  -H 'Content-Type: application/json' \
  -d '{"handle": "r_abc123/result", "window": {"kind": "jsonpath", "expr": "$.message"}}'
```

All responses use the operator envelope: `{ "issued": bool, "body": { ... } }`.

---

## 2. UI routes

| Route | Job | Operator call |
|-------|-----|---------------|
| `/` | Connection health | `read_health` |
| `/host-wiring` | MCP stdio snippet | `read_host_wiring` |
| `/sessions` | Recent runs / failures tabs | `read_session_rows` |
| `/sessions/{handle}/ask` | Bounded ask + actions | `read_session_rows`, chunk, cancel/pin |
| `/registry` | Plugin catalog | `iter_registry` |
| `/registry/{name}` | Plugin detail | `describe_registry_entry` |

The ask page shows **chunks**, not a live stream. Truncation and continuation handles are displayed when present.

---

## 3. Honesty rules (same as MCP)

### `projection.not_finalized` (R-QB-28)

While evidence is not finalized, stream views (`last_error`, `run_tail`, …) refuse with `projection.not_finalized`. The UI shows **“telemetry not filed yet”** — it does not fake partial streams.

### Admission refusals

`admission.*` outcomes never include `run_id`. Operator HTTP does not expose admit or `run`.

### Retrieval-first

Prefer the smallest answer: result jsonpath for success, `last_error` for failure, artifact `fetch` for files. No log scroller, no live tail.

---

## 4. CLI vs operator HTTP

| Task | Surface |
|------|---------|
| Health, retention, recovery | `trestle doctor`, `pin`, `unpin`, `recover` |
| Browse sessions, bounded telemetry | `trestle ops serve` + UI or `/ops/v1` |
| Agent retrieval | `trestle serve` MCP — not operator HTTP |

`query` / `fetch` are **not** CLI subcommands in v0.1. Humans use operator HTTP; agents use MCP.

---

## 5. Smoke verification

```bash
pip install -e ".[dev]"
python scripts/smoke_operator_api.py
```

Expected: `SMOKE OK` with a sample `run_id`, `recent_runs` row, and telemetry chunk.

Full closure check:

```bash
pytest -q
python scripts/smoke_agent_mcp.py
python scripts/smoke_operator_api.py
trestle ops serve   # open http://127.0.0.1:18733
```

---

## 6. Operator v0.2 (complete)

Registry browser, `recent_failures` tab, host wiring snippet, pin/cancel/join from Ask UI, retrieval chain panel. See [`hld/spec-operator-sessions-telemetry.md`](../hld/spec-operator-sessions-telemetry.md) §3–4.

## 7. Out of scope

- Admit / `run` from browser
- Live tail / websocket streams
- Porch naming (`/porch/v1`, `console/porch/`)
- Replacing agent stdio MCP with HTTP
