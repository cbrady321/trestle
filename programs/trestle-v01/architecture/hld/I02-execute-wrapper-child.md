# I02 — Execute ↔ Wrapper ↔ Child

**C-id:** C-I02-EXEC-CHILD  
**Owner:** trestle-m1-kernel-skeleton  
**Consumers:** trestle-m2-bounding (capture limits), trestle-m3-durability (SIGKILL/cancel)

## Contract

1. **Spawn-per-run** — one child per run; no fork-after-warm-import (R-EXEC-34: one quiet wrapper per snapshot).
2. **Wrapper** — quiet poll/select reactor; MUST NOT import FastMCP or plugin deps.
3. **RunSpec** — sealed letter to child; not Admit/Project currency.
4. **Child** — foundation runtime + script as distinct modules; script sees PluginSurface only.
5. **Automatic runtime (R-AUTO-1–7)** — cwd=`work/`, `TMPDIR`=`work/tmp`, `outputs/` auto-promote, stdout/stderr/logging captured.
6. **LedgerCommand** — child classification → ledger; not MCP types.

## Evidence layout (M1 minimum)

```
evidence/<run_id>/
  meta.json          # non-authoritative
  ledger events      # ndjson authority
  stdout/, stderr/
  result.json        # raw return (M2: index projection reads this)
```

## Unit obligations

| Unit | Produces | Consumes |
|------|----------|----------|
| M1 | wrapper+child spawn, poll capture, PluginSurface | — |
| M2 | `result.index`, capture limits on I02 byte streams | I02 evidence paths |
| M3 | Process-group cancel, timeout | I02 wrapper reactor |

## Verifier

Wrapper and child processes MUST NOT import `fastmcp`. Script validation rejects Kernel imports (R-PLUG-6).
