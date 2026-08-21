# I01 — Admit ↔ Scheduler ↔ Execute

**C-id:** C-I01-ADMIT-EXECUTE  
**Owner:** trestle-m1-kernel-skeleton  
**Consumers:** trestle-m1-kernel-skeleton (Conductor), trestle-m3-durability (cancel)

## Contract

1. **Admit.admit(AdmitRequest) → AdmitResult** — tagged union; refusal has **no** `run_id`.
2. **Scheduler** mints WorkOrder only after durable ledger `created`.
3. **Conductor** is sole Execute consumer; MCP/CLI never call Execute.
4. Queue depth 256; excess → `admission.queue_full` at Admit.
5. Drain: `admission.service_draining` for new work; running runs get grace.

## Types (freeze excerpt)

```python
class AdmitRequest:
    plugin_id: str
    args: dict
    idempotency_key: str | None

class AdmitResult:  # Refused | Admitted(Handle)
    ...
```

## Unit obligations

| Unit | Produces | Consumes |
|------|----------|----------|
| M1 | Full I01 skeleton | — |
| M3 | Cancel/timeout on running WorkOrders | I01 WorkOrder lifecycle |

## Verifier

Admit path never imports Execute types into Door responses. WorkOrder exists only after ledger append succeeds.
