# Agent MCP usability assessment

**Status:** C1 complete — C2 shipped (`docs/agent-console-mcp.md`).  
**Date:** 2026-08-25  
**Method:** Scripted FastMCP stdio client against `trestle serve` (two sessions: empty vs fixture plugin dir), plus `ControlSurface` direct calls and existing pytest corpus (102 passed).  
**Scope:** How agents should use `trestle serve` in real hosts — wiring, workflow, friction — without reopening the nine-tool freeze unless a gap is proven.

---

## Executive summary

**Recommendation:** Ship **docs-first** agent onboarding — a single playbook (`docs/agent-console-mcp.md`) with copy-paste host JSON, a retrieval decision tree, and the freeze mis-invocation table. Add a repo `.cursor/mcp.json` example and a one-paragraph README pointer. **No kernel or freeze changes** for Phase C.

The nine-tool surface is **technically sound** and **G1-compliant** (2,365-byte `tools/list`, nine tools, schemas pulled via `describe_plugin`). The main gaps are **discoverability** (view names, retrieval paths, plugin install location) and **one behavioral mismatch** between freeze/HLD `wait_ms` semantics and the current blocking `run` implementation.

| Dimension | Score (1–5) | Verdict |
|-----------|-------------|---------|
| Host attach | 2 | Works via stdio; zero in-repo wiring |
| Cold start / G1 | 5 | Nine tools, 2.4 KB definitions |
| Run → wait → terminal | 3 | Happy path works; `wait_ms` non-blocking semantics not realized |
| Console retrieval | 3 | `fetch`/`query` work; path choice not self-evident |
| Refusals & honesty | 4 | Codes actionable; `not_finalized` hard to hit via MCP |
| Plugin discovery | 4 | Pull model good; fresh home has no plugins |
| **Overall** | **3.5** | **Docs + host snippet unblock C2; no freeze amendment** |

---

## Evidence sessions

### Session A — empty `TRESTLE_HOME` (onboarding failure mode)

- Transport: `StdioTransport(command=python, args=["-m", "trestle.cli", "serve"])`
- `tools/list`: 9 tools, **2,365 bytes** (budget 8,192)
- `list_plugins`: 0 items (`registry_version: 1`)
- `run(plugin="echo")` → `admission.plugin_not_found` (no `run_id`) ✓
- Refusals: `fetch("/etc/passwd")` → `projection.invalid_handle`; `query(view="plugin_stats")` → `projection.invalid_view` ✓

**Finding:** A fresh install with default `~/.trestle` and no `plugins/` directory gives agents no plugins and no hint where to put them.

### Session B — fixture plugins + limits (`TRESTLE_TEST_LIMITS=1`)

- Plugins: `big_array`, `drop_in`, `echo`, `hostile`, `outputs_writer`, `slow`
- `run(echo, wait_ms=5000)` → terminal `RunView` in one call (`state: succeeded`, `summary.message: assess`) ✓
- `query(last_error)` on success → empty items (expected)
- `query(run_tail)` on echo → 0 lines (echo logs via `ctx.log`, not wrapper stdout)
- `fetch("{run_id}/result", jsonpath $.message)` → `["assess"]` ✓
- `run(hostile)` → `limits_exceeded` markers on `RunView` (`stdout`, `events` streams) ✓
- `run(failer)` → `state: failed`; `query(last_error)` → one row (`kind: failed`) ✓
- `describe_plugin("echo")` → name, version, `input_schema`, snapshot metadata ✓

### Session C — blocking `run` timing (`ControlSurface` direct)

```
wait_ms=0  on slow(seconds=2) → elapsed 2.35s, state=succeeded
wait_ms=500 on slow(seconds=2) → elapsed 2.34s, state=succeeded
```

`ControlSurface.run` always calls `conductor.drive()` synchronously before honoring `wait_ms`. The MCP `run` tool **blocks for the full plugin duration** regardless of `wait_ms`. Early return with a `running` frame (HLD § Semantic Contract, R-WAIT-1) is **not reachable** via MCP today.

---

## Dimension scores

### 1. Host attach — **2/5**

| Check | Result |
|-------|--------|
| Stdio spawn works | ✓ Scripted client connected and called all nine tools |
| Repo `.cursor/mcp.json` | ✗ Missing |
| `docs/` playbook | ✗ Missing (C2) |
| README MCP pointer | ✗ `readme` is `trestle-requirements.md`; no serve wiring |
| FastMCP startup banner on stderr | Present (cosmetic; did not break client) |

**Working snippets (assessment-validated):**

```json
{
  "mcpServers": {
    "trestle": {
      "command": "python3",
      "args": ["-m", "trestle.cli", "serve"],
      "env": {
        "TRESTLE_HOME": "~/.trestle"
      }
    }
  }
}
```

Use the absolute path to the Python env where `trestle` is installed (`pip install -e .`). Claude Desktop uses the same shape under `mcpServers`.

**Friction:** Agents and operators have no in-repo starting point. First-run with empty `~/.trestle/plugins` looks like a broken server.

**Remediation (ranked):** docs playbook §0 host attach → copy-paste JSON in repo → README pointer. No skill required for wiring; optional Cursor rule pointing at playbook.

---

### 2. Cold start / `tools/list` footprint (G1) — **5/5**

| Metric | Value |
|--------|-------|
| Tool count | 9 (frozen set) |
| Serialized definitions | **2,365 bytes** |
| Budget (R-VER-8 / test) | 8,192 bytes |
| Avg description length | ~35 chars |
| Plugin schemas on `tools/list` | None (`list_plugins` / `describe_plugin` pull model) |
| `registry_version` on list | Mirrored via `attach_registry_version_mirror` |

Descriptions are intentionally minimal. **Risk:** `query` description is “Query a named view” with no enum of view names — agents cannot discover `run_tail` vs `last_error` from `tools/list` alone.

**Remediation:** docs only — embed the nine `ViewName` values and when to use each. No tool-description inflation (anti-G1).

---

### 3. Run → wait → terminal workflow — **3/5**

| Path | Works? | Notes |
|------|--------|-------|
| `list_plugins` → `run` → terminal | ✓ | 5-turn golden path (see `test_m7_workflows`) |
| `run` + `wait_ms` early return | ✗ | `drive()` blocks; `wait_ms` ignored for duration |
| `await_runs` after `run` | Redundant | Same process cannot start a background run via MCP |
| `cancel` on long run | Untested live | Kernel tests cover cancel; MCP wiring exists |
| Idempotency key | Untested in session | Covered in HLD S5; no MCP session exercise |

**Agent stall patterns (predicted):**

1. **Long `run` blocks the MCP tool call** — host may time out before plugin finishes; agent cannot interleave other work in the same session.
2. **`wait_ms=0` does not mean “admit and return”** — agents expecting async admit will still wait for completion.
3. **Calling plugin name as MCP tool** — protocol unknown-tool; playbook must say “always `run(plugin=…)`”.
4. **Re-querying before terminal** — `projection.not_finalized` is correct but rare via MCP because `run` usually returns terminal.

**Remediation (ranked):**

1. **Docs** — document actual blocking behavior; recommend short `wait_ms` only for “already terminal” semantics; note host timeout risk for long jobs.
2. **Tests** — optional MCP stdio integration test documenting blocking contract.
3. **Freeze amendment (last resort)** — non-blocking `run` (admit + background `drive`, honor `wait_ms`) as a **separate freeze unit** if owner requires true G6 non-blocking joins. Not needed to ship C2 playbook.

---

### 4. Console retrieval — **3/5**

| Job | Best path | Session evidence |
|-----|-----------|------------------|
| Plugin return value | `fetch("{run_id}/result", {kind: "jsonpath", expr: "…"})` | ✓ `["assess"]` |
| Failure diagnosis | `query(last_error, {run_id})` | ✓ failed run → one row |
| Wrapper stdout lines | `query(run_tail, {run_id})` | ✓ hostile → 5 lines; echo → 0 (no print) |
| Large array body | `RunView.summary.handle` + `fetch` jsonpath/range | ✓ `test_m2_bounding` |
| Artifact bytes | `query(run_artifacts)` → `fetch(art_…, {kind: head\|tail\|grep})` | ✓ `test_m7_workflows` |
| Pattern in stdout | **`query(run_tail)`** (bounded rows), not `fetch` on `console/*` | `fetch("{run_id}/console/stdout")` → `invalid_handle` |

**Discoverability gap:** Plan text mentions `fetch` grep for console evidence; freeze routes **wrapper pipes through `query(run_tail)`**, while `fetch` grep applies to `art_…` and `{run_id}/result` handles. Agents will mis-invoke without a decision tree.

**Remediation:** docs playbook §2 retrieval tree (primary). No new tools.

---

### 5. Refusals & honesty — **4/5**

| Case | Code | `run_id`? | Actionable? |
|------|------|-----------|-------------|
| Unknown plugin | `admission.plugin_not_found` | No | ✓ pick valid name from `list_plugins` |
| Path fetch | `projection.invalid_handle` | N/A | ✓ use handle from `RunView` / query |
| Unknown view | `projection.invalid_view` | N/A | ✓ use frozen view names |
| Not finalized | `projection.not_finalized` (`retryable: true`) | N/A | ✓ `await_runs` — rarely seen after MCP `run` |
| Limits suppressed | `RunView.limits_exceeded[]` | In `RunView` | ✓ check after hostile/large runs |
| Scan budget | `FetchSlice.truncated` + `scan_bytes` | N/A | ✓ narrow window (not `isError`) |

Admission refusals correctly omit `run_id`. Messages are short and machine-readable.

**Gap:** `not_finalized` playbook is hard to exercise in MCP-only flows because `run` blocks until finalize. Document anyway for `query(recent_runs)` + still-running edge cases and future non-blocking `run`.

---

### 6. Plugin discovery — **4/5**

| Check | Result |
|-------|--------|
| `list_plugins` size | Small (`CatalogView`: names, versions, `capability_class`) |
| `describe_plugin` pull | ✓ Full `input_schema` for one plugin |
| Over-fetch risk | Low if agents follow list → describe one |
| Hot reload | ✓ `registry_version` bumps (`test_m6_extending`) |
| Param naming | `run(plugin=…)` vs `describe_plugin(plugin_id=…)` — minor inconsistency |

**Friction:** Default `TRESTLE_HOME/plugins` must exist and contain `.py` plugins. No `trestle init` or sample plugin in repo for agents.

**Remediation:** docs §0 bootstrap (`mkdir -p ~/.trestle/plugins`, copy example plugin); optional example plugin file in repo (not a new tool).

---

## Friction inventory & remediation ranking

| # | Friction | Severity | Remediation | Freeze? |
|---|----------|----------|-------------|---------|
| F1 | No host wiring in repo | High | Docs + `.cursor/mcp.json` example | No |
| F2 | Empty plugin dir on first run | High | Docs bootstrap + example plugin | No |
| F3 | View names not on `tools/list` | Medium | Playbook view table | No |
| F4 | `run_tail` vs `fetch` confusion | Medium | Playbook retrieval tree | No |
| F5 | `run` blocks entire call | Medium–High | Document; defer non-blocking `run` | **Only if owner requires** |
| F6 | `wait_ms` semantics misleading | Medium | Document actual behavior | **Only if owner requires** |
| F7 | `not_finalized` rare via MCP | Low | Document for completeness | No |
| F8 | FastMCP stderr banner | Low | Ignore / note in docs | No |
| F9 | `plugin` vs `plugin_id` param names | Low | Playbook alias note | No |

**Remediation precedence (binding for C2):**

1. **Docs** — playbook is the primary deliverable.
2. **Host snippet** — checked-in MCP JSON template.
3. **Skill / Cursor rule** — optional; link to playbook, do not duplicate freeze.
4. **Example project** — single `plugins/example.py` or documented copy from tests/fixtures.
5. **Pytest MCP harness** — optional regression for stdio session (session B assertions).
6. **Freeze amendment** — **last resort**, owner sign-off: non-blocking `run` + true `wait_ms` / `await_runs` composition.

---

## Recommendation

### Primary path

**Documentation-led agent onboarding** with validated stdio config:

1. Publish `docs/agent-console-mcp.md` (C2) as the single agent SSOT.
2. Add `.cursor/mcp.json` (or `docs/mcp/cursor.json`) with the snippet above.
3. Add README paragraph linking the playbook and `pip install -e .` + plugin bootstrap.

Kernel behavior is **sufficient for v0.1 console retrieval** given honest documentation. The nine tools do not need changing.

### Alternates (not primary)

| Alternate | When |
|-----------|------|
| Cursor skill summarizing retrieval tree | Team uses skills heavily |
| CLI-only operators | Continue `trestle query` / `trestle fetch` / `trestle doctor` — out of agent path |
| Non-blocking `run` (freeze unit) | Host tool timeouts or true parallel runs become blocking adoption |

### Out of scope (explicit)

- Tenth MCP tool or HTTP BFF / Porch adapter
- `console/` web tree or human dashboard
- Live `run_tail` on running runs (R-QB-28)
- Per-plugin MCP tools or inlined schemas on `tools/list`
- Changing nine tool names or admission rules
- Sandbox / remote execution patterns
- Kernel changes for Phase C unless F5/F6 are escalated by owner

---

## Playbook outline for C2 (`docs/agent-console-mcp.md`)

The playbook should implement this outline verbatim in structure:

### §0 — Attach & bootstrap

- Install: `pip install -e ".[dev]"`
- `TRESTLE_HOME` default `~/.trestle`
- Create `~/.trestle/plugins/`; add or copy a plugin file
- Cursor / Claude Desktop JSON (from this assessment)
- Verify: `trestle doctor`; host shows nine tools

### §1 — Golden workflow (≤5 turns)

1. `list_plugins` — pick `name`
2. `describe_plugin(plugin_id)` — if args unknown
3. `run(plugin, args, wait_ms=…)` — expect **blocking** until terminal today
4. On success: `fetch("{run_id}/result", …)` for return value
5. On failure: `query(last_error, {run_id})` first

### §2 — Retrieval decision tree

| Question | Tool | View / window |
|----------|------|----------------|
| What did the plugin return? | `fetch` | `{run_id}/result` + jsonpath/range |
| Why did it fail? | `query` | `last_error` |
| What printed to stdout? | `query` | `run_tail` (not live tail) |
| Large array/object? | `fetch` | `summary.handle` from `RunView` |
| Artifact file? | `query` → `fetch` | `run_artifacts` → `art_…` |
| TTY-class output? | `fetch` | `art_…` (not `run_tail`) |
| Data suppressed? | inspect `RunView` | `limits_exceeded` |

### §3 — Wait & honesty

- `run` blocks until plugin completes (current behavior)
- `await_runs` for joining handles already admitted elsewhere
- `projection.not_finalized` → wait, then retry query
- `limits_exceeded` on status frame → evidence truncated by design
- Admission errors never include `run_id`

### §4 — Mis-invocation table

Copy from `hld-interface-architecture-trestle.md` § Mis-invocation playbook (paths, live tail, plugin-as-tool, idempotency).

### §5 — Smoke commands (CLI mirror)

```bash
trestle query recent_runs
trestle query run_tail --run-id <handle>   # after finalize
trestle fetch <handle>/result --window '{"kind":"jsonpath","expr":"$"}'
```

### §6 — Operator vs agent

- Agents: MCP only for evidence
- Humans: CLI richer (`doctor`, pin, recover) — same admission rules

---

## Gates C2

| C2 section | Source in this assessment |
|------------|---------------------------|
| Host attach | §1 Host attach + recommendation |
| Golden flow | §3 Run workflow + playbook §1 |
| Retrieval tree | §4 Console retrieval + playbook §2 |
| Refusals | §5 + playbook §3–§4 |
| Plugin bootstrap | §6 + playbook §0 |
| Smoke verification | Session B + playbook §5 |

**C2 is unblocked.** No freeze amendment required for playbook shipment.

---

## Appendix — tool manifest (session B)

| Tool | Description (truncated) |
|------|-------------------------|
| `run` | Start a plugin run and optionally wait for a status frame. |
| `await_runs` | Wait for existing runs to reach a terminal state. |
| `cancel` | Request cancellation of a run. |
| `query` | Query a named view. |
| `fetch` | Fetch bytes for a handle within a window. |
| `pin` / `unpin` | Retention |
| `list_plugins` | List published plugins. |
| `describe_plugin` | Describe one plugin including input schema. |

**View names (for playbook):** `run`, `last_error`, `run_tail`, `run_events`, `recent_runs`, `recent_failures`, `run_provenance`, `run_artifacts`, `artifact_refs`.
