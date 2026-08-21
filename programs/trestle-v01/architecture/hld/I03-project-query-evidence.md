# I03 — Project ↔ Query ↔ Evidence

**C-id:** C-I03-PROJ-QUERY  
**Owner:** trestle-m2-bounding (Project projection), trestle-m5-query (backend)  
**Consumers:** MCP ControlSurface, trestle-m4-waiting (await frames)

## Contract

1. **Projection law (R-INV-1)** — Only Project emits toward MCP. Build summaries from `result.index` + `pread`; never slurp `result.json`.
2. **DefaultAgentSuccess** — status frame always; verbatim summary iff within budget; handles for truncation.
3. **Fetch** — Handle-addressed slices; path-shaped strings → `projection.invalid_handle`.
4. **Query backend** — derived cache; nine named views; `backend`/`as_of` on envelopes; R-QB-28: no live tail/events on running runs.
5. **Abandoned artifacts fetchable by default** (§25.6).

## View set (v0.1 freeze)

Nine views per requirements; no `plugin_stats`. Catalog is `CatalogView`, not a query view.

## Unit obligations

| Unit | Produces | Consumes |
|------|----------|----------|
| M2 | result.index, summary.json, fetch, pins | I02 evidence |
| M4 | await_runs partial sequences | I03 RunView frames |
| M5 | fs backend, view conformance | I03 evidence layout |

## Verifier

MCP process never reads raw `result.json` for agent payloads. Query rows obey ViewRow truncation (R-QB-25).
