# Trestle v0.1 Performance Assessment (M7)

Structural workflow benchmarks from `tests/test_m7_workflows.py` compare the
recommended agent path against a naive poll baseline.

## Benchmark workload (US-01 style)

Agent path (5 tool turns):

1. `list_plugins`
2. `run` with `wait_ms=5000` (terminal within wait, or `running` frame on timeout)
3. `await_runs` (join confirmation; already terminal)
4. `query` (`recent_runs`)
5. `fetch` (bounded slice via handle)

Naive baseline (8 turns): poll `status`/`query` in a loop without `wait_ms` or
`await_runs` join semantics.

## Result

| Metric | Agent workflow | Naive baseline |
|--------|----------------|----------------|
| Model turns | 5 | 8 |
| Turn reduction | 37.5% | — |

The agent workflow satisfies R-VER-4–5 structurally: single-turn paths exist for
`run(wait_ms>0)` and `await_runs`, avoiding turn-per-poll behavior (G6, US-04).

## Rigor suites (post-M7)

| Suite | Tests | Oracle |
|-------|-------|--------|
| `test_ver_serialization.py` | 14 | R-VER-6–8 canonical JSON, all result shapes, tools/list ≤8192B |
| `test_user_stories.py` | 20 | US-02–US-24 core paths (TTY via catalog absence US-16) |

**Total:** 102 pytest tests (68 M1–M7 + 34 rigor).

- λ (token-equivalent cost per turn) is not calibrated numerically in v0.1
  (R-COST-3); turn count is the M7 proxy.
- Full token benchmarking requires a pinned tokenizer and named workload corpus
  (deferred follow-on).
