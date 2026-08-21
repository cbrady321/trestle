# Spike results (M0 / M0.5 / M0.6)

Recorded 2026-08-19. Harnesses: `spikes/m0_mcp_protocol.py`, `spikes/m05_supervisor_fork.py`, `spikes/m06_projection.py`.
Python 3.12.8, FastMCP 3.4.7, NumPy 2.5.2, pandas 3.0.5.

## Decision

**v0.1 execution is spawn-per-run.** Do not import plugin dependencies in a process that will `fork()`.

## M0.5 — process architecture

| | macOS (darwin) | Linux (Docker python:3.12-bookworm) |
|---|---|---|
| OS threads after import only | 1 | 1 |
| OS threads after `numpy` matmul | **4** | **16** |
| Python threads | 1 | 1 |
| Python 3.12 `fork()` warning | "process is multi-threaded, use of fork() may lead to deadlocks" | same class of warning (Linux run used fork after compute) |
| Poll capture of stdout | pass | pass |
| Fork after previous child | pass (undefined) | pass (undefined) |
| Concurrent children | pass | pass |
| Async plugin in child | pass | pass |
| Subprocess grandchild | pass | pass |
| Process-group SIGKILL | pass | pass |
| Cold spawn import (cached) | 706 ms | 619 ms |
| Fork child after warm import | ~130 ms | ~118 ms |

Fork *appeared* to work after NumPy started a native pool. That is not viability: the interpreter itself classifies the parent as multithreaded. Warm-fork is an optimization with a substantial portability risk and is **not** the v0.1 default.

Quiet poll/select capture **is** viable and is retained. The wrapper that polls **must not** import NumPy/pandas/plugin code.

Raw JSON: `spikes/out/m05-darwin-3.12.json`, `spikes/out/m05-linux-3.12.json`.

## M0.6 — projection

All cases passed.

- 8e6-element streamed array → `result.json` **62_888_891 bytes**; `result.index` **329 bytes** (`index_truncated`); summary **114 bytes**.
- 2 MB scalar string → summary **90 bytes**, string not inlined.
- 20_000-key object coarsens the index to ≤ 64 KB.
- NaN/Infinity rejected; abrupt truncated JSON is invalid.

Projection via index + `pread` is implementable. Raw: `spikes/out/m06/report.json`.

## M0 — FastMCP 3.4.7 / MCP SDK

- **stdio `tools/call` works** (in-process FastMCP transport and `StdioTransport`).
- `tools/list` returns `ListToolsResult` with `nextCursor`; **no `ttlMs` / `cacheScope`** on this pin.
- `@tool(task=True)` requires `fastmcp[tasks]`, which depends on **pydocket**. FastMCP task handlers talk to **Docket/Redis**. That conflicts with R-FMC-8.
- Trestle **must not** use FastMCP's task executor. Agent wait in v0.1 is `wait_ms` / `await_runs` on stdio. A Trestle-owned Tasks projection (no Docket) remains optional and M0-gated if a target client requires it.

Raw: `spikes/out/m0-protocol.json`.
