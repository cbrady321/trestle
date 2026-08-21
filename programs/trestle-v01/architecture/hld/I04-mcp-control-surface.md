# I04 — MCP ↔ ControlSurface

**C-id:** C-I04-MCP-CS  
**Owner:** trestle-m1-kernel-skeleton  
**Consumers:** All MCP agent workflows

## Contract

1. **Nine tools only:** `run`, `await_runs`, `cancel`, `query`, `fetch`, `pin`, `unpin`, `list_plugins`, `describe_plugin`.
2. **FastMCP 3.4.7 stdio only** — pin enforced; no http_app, no Docket/Tasks executor.
3. **adapter_coverage** — explicit map tool → ControlSurface member; missing row = authorized lag.
4. **`run` composition** — Admit then optional Project.await_one with `wait_ms` (ControlSurface policy, not AdmitRequest field).
5. **Thin tool definitions** — plugin JSON schemas off `tools/list`; `describe_plugin` is pull-for-one.
6. **`registry_version`** — CatalogView freshness; mirrors `tools/list` when present.

## Dual-error contract

Request refusals (`RequestOutcome`) ≠ run failures (`RunView`). No `run_id` on refusal.

## Unit obligations

| Unit | Produces | Consumes |
|------|----------|----------|
| M1 | FastMCP porch + ControlSurface skeleton | I01, I03 (status frame) |
| M4 | wait_ms / await_runs wiring | I04 tool handlers |
| M6 | list_plugins registry_version on reload | I04 catalog |

## Verifier

`tools/list` byte budget stable as plugin count grows (R-VER-8). Wrapper/child never import FastMCP.
