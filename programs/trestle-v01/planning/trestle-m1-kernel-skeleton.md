# M1 — Kernel Skeleton (light plan)

**Unit:** trestle-m1-kernel-skeleton  
**Milestone exit:** Runnable `run` → terminal RunView for trivial plugin

## Scoped requirements

M1 milestone; R-INV-1 skeleton; R-AUTO-1–7; R-BOUND-1–6; R-EXEC-* spawn/capture; R-FMC-1–3,6–8; R-MCP-1 (nine tools registered, schemas thin); R-REG-* snapshots; §5 ledger ndjson.

## Module targets

```
trestle/server/   admission, scheduler, ledger, registry, snapshots, main (FastMCP)
trestle/wrapper/  main, reactor, spawn
trestle/child/    main, context (+ extend serialize/index)
trestle/plugin/   surface.py
trestle/common/   extend types, spec, codes
tests/            fixtures/plugins/, M1 smoke tests
```

## Interface obligations

- I01, I02, I04 full skeleton
- I03 status frame only (no fetch/query yet)

## Brownfield

Replace/extend `trestle/common/*`, `trestle/child/*`; implement missing `trestle/server/*`; fix `cli.py` imports.

## Tests

- Plugin fixture: print, log, write outputs/
- Refusal: unknown plugin → no run_id
- Spawn: wrapper/child do not import fastmcp
