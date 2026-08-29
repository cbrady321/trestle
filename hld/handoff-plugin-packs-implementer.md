# Handoff — plugin packs implementer

> **Successor prompt.** Plugin packs v0.1 is **verified on disk** — land, adopt, or extend.  
> **Parent SSOT:** [`trestle-requirements.md`](../trestle-requirements.md) — **§1 Goals** (G3: extend tool surface without touching server; G1: minimize agent context per unit of work) and **§2.3 Non-goals** (not a workflow engine).  
> **This-node goal:** [`hld/design-plugin-packs.md`](design-plugin-packs.md) — **Goal** — pre-built workflow packs agents invoke via `run(plugin=…)` with wave ordering, readiness waits, and grep-able artifacts, without new MCP tools.

---

## Identity

**Initiative:** Trestle plugin packs (Docker, pytest, migrations)  
**Phase:** v0.1 verified — land / adopt / optional extension  
**Baseline:** `packages/trestle-packs/` v0.1.0, `examples/packs/` (4 plugins), `docs/packs.md`, CI smoke wired  
**Git:** `master` @ `51ae8b6` — plugin-packs work **uncommitted** (large working-tree diff)

---

## Goals (cascade)

**Parent** ([`trestle-requirements.md`](../trestle-requirements.md) **§1 Goals**, **§2.3 Non-goals**):

- Agents run local work through bounded MCP projections, not unbounded shell context (G1, G4)
- Extend capability by writing plugins — no server restart or new MCP tools (G3)
- Kernel stays a ledger + ten-tool porch; **not a workflow engine** (§2.3)

**This-node** ([`hld/design-plugin-packs.md`](design-plugin-packs.md) **Goal**):

- Ship `trestle_packs` library + example `@trestle` plugins for Docker stacks, pytest, and migrations
- Declarative wave specs for dependency-ordered container startup with readiness probes
- All durable output via `ctx.log` / `ctx.attach`; agent retrieval unchanged (`query` / `fetch`)

**Successor job:** Commit and land the uncommitted plugin-packs work, then drive agent adoption with `docs/packs.md` specs — or pick up optional dokker adapter if richer health-check DSL is needed.

---

## Current state

### Done

| Item | Location |
|------|----------|
| `trestle-packs` package | `packages/trestle-packs/` |
| Core DAG, readiness, artifacts, teardown (STOP/DOWN/NONE) | `trestle_packs/core/` |
| Docker `StackSpec` + `StackRunner` + per-service log artifacts | `trestle_packs/docker/` |
| python-on-whales `DockerClient` backend (API-correct) | `compose_whale.py` |
| Pytest in-process runner + JSON report | `trestle_packs/pytest/` |
| Alembic + Yoyo runners | `trestle_packs/migrate/` |
| Example plugins | `examples/packs/{docker_stack,pytest_run,migrate_apply,integration_pipeline}.py` |
| Fake backend + stack runner integration tests | `packages/trestle-packs/tests/` |
| Live Docker test (daemon-gated skip) | `test_docker_integration.py` |
| `scripts/smoke_packs.py` + CI | `scripts/`, `.github/workflows/ci.yml` |
| Agent quick-start | `docs/packs.md` |
| Root `[packs]` optional-dep | `pyproject.toml` → `trestle-packs[all] @ file:packages/trestle-packs` |
| Pack import validation + `catalog_hint` | `trestle/server/plugin_validate.py` |
| `admission.import_failed` at admit time | `trestle/server/admission.py` |
| Pipeline teardown on failure | `examples/packs/integration_pipeline.py` |
| Successor verify block passed | 2026-08-29 — see evidence below |

### Not done

| Item | Notes |
|------|-------|
| Git commit / push / PR | All v0.1 work is uncommitted on `master`; blocks adoption outside workspace |
| dokker adapter (optional) | Richer health-check DSL behind `trestle_packs.docker.dokker_adapter` |
| PyPI publish (optional) | Monorepo-only today; `trestle[packs]` path dep is the install story |

---

## Threads

1. **Library vs plugin boundary** — `trestle_packs` is importable Python; `examples/packs/` are thin `@trestle` wrappers. Custom project plugins copy `integration_pipeline.py`.

2. **OSS backends** — python-on-whales (Docker), pytest hooks, Alembic/Yoyo programmatic APIs. No Kernel imports in pack code.

3. **Agent path unchanged** — `list_plugins` → `run` → `await_runs` → `query`/`fetch`. Ten MCP tools only.

4. **Validation is admit-time** — missing `trestle_packs` → `valid: false` in catalog, `catalog_hint`, and `admission.import_failed` (no `run_id`).

---

## Constraints

- Ten MCP tools only — packs expose plugins, not new tools
- `ctx.run_cmd` spec'd but **not implemented** in kernel — packs use subprocess/libraries
- No live tail during runs — agents `await_runs` then query artifacts
- Docker required on host for `docker_stack` / `integration_pipeline`
- R-PLUG-20: optional deps must be installed (`pip install -e ".[packs]"`)

---

## Next steps (ordered)

1. Commit plugin-packs work (separate pack feature from unrelated `hld/` / `programs/` deletions if needed)
2. Push and open PR (or push to `master` per team workflow); confirm CI green
3. Agent adoption: `trestle serve --plugin-dir examples/packs`; copy-paste specs from `docs/packs.md`
4. (Optional) dokker adapter for custom probe DSL beyond `WaitMode.CUSTOM`

---

## Open questions

1. Monorepo publish — standalone PyPI `trestle-packs` vs `trestle[packs]` only?
2. Should CI require Docker daemon (currently integration test skips when daemon down)?
3. Should the `hld/` and `programs/` deletions ship in the same commit as plugin packs?

---

## References

| Doc | Role |
|-----|------|
| [`hld/design-plugin-packs.md`](design-plugin-packs.md) | Pack architecture + declarative spec |
| [`docs/packs.md`](../docs/packs.md) | Agent copy-paste `run` specs |
| [`docs/agents.md`](../docs/agents.md) | Golden MCP workflow |
| [`trestle-requirements.md`](../trestle-requirements.md) | Kernel constraints |
| [`packages/trestle-packs/README.md`](../packages/trestle-packs/README.md) | Install |

---

## Check-in report

```yaml
header:
  initiative: Trestle plugin packs
  generated: "2026-08-29"
  scope: Docker / pytest / migration workflow libraries + example plugins
  resolved_baseline: packages/trestle-packs v0.1.0 @ examples/packs + docs/packs.md
  git_head: "51ae8b6 on master (uncommitted working tree)"

executive_summary: >
  Plugin packs v0.1 is complete and verified on disk. The trestle_packs library
  provides wave-ordered Docker orchestration, in-process pytest, and Alembic/Yoyo
  migration runners. Four example plugins ship in examples/packs/. Kernel admits
  pack plugins only when trestle_packs is installed. Successor verify block
  passed (8+142 tests, PACKS SMOKE OK). The remaining gate is git land — all
  work is uncommitted on master.

done:
  - packages/trestle-packs/ library (core, docker, pytest, migrate)
  - examples/packs/ four @trestle plugins
  - FakeComposeBackend + StackRunner integration tests
  - Live Docker integration test (skips when daemon unavailable)
  - compose_whale DockerClient API fix
  - scripts/smoke_packs.py + CI wiring
  - docs/packs.md agent quick-start
  - pyproject.toml [packs] path dependency
  - plugin_validate.py + admission.import_failed
  - integration_pipeline teardown on failure
  - successor verify block passed 2026-08-29

remaining:
  - item: git commit / push / PR
    where: git (master, uncommitted)
    blocks: adoption outside this workspace
  - item: dokker adapter (optional)
    where: packages/trestle-packs/trestle_packs/docker/
    blocks: richer health-check DSL only — not a v0.1 gate

challenges:
  open_issues:
    - ctx.run_cmd not implemented — packs use subprocess/libraries
    - Docker daemon required on host for live docker_stack runs
    - working tree mixes plugin-packs additions with hld/ and programs/ deletions
  burn_down:
    - v0.1 implementation and verify are closed
    - land commit is the only v0.1 gate
    - adoption path is docs/packs.md + trestle serve --plugin-dir examples/packs

next_action: >
  Commit the plugin-packs work, push, and open a PR (or push to master);
  confirm CI green, then point agents at examples/packs via docs/packs.md.

evidence:
  design: hld/design-plugin-packs.md
  agent_docs: docs/packs.md
  library_root: packages/trestle-packs/trestle_packs/
  plugins: examples/packs/
  pack_tests: "8 passed, 1 skipped (python -m pytest packages/trestle-packs/tests/)"
  kernel_tests: "142 passed (python -m pytest tests/)"
  smoke: "PACKS SMOKE OK (python scripts/smoke_packs.py)"
  verify_date: "2026-08-29"
```

---

## Verify

```bash
pip install -e ".[dev,packs]"
python -m pytest packages/trestle-packs/tests/ -q    # 8 passed, 1 skipped
python -m pytest tests/ -q                           # 142 passed
python scripts/smoke_packs.py                        # PACKS SMOKE OK
trestle serve --plugin-dir examples/packs
```

---

## Successor prompt (copy-paste)

> Pick up **plugin packs v0.1** — verified on disk, not yet committed.  
> **Parent:** [`trestle-requirements.md`](../trestle-requirements.md) §1 Goals + §2.3 Non-goals.  
> **This-node:** [`hld/design-plugin-packs.md`](design-plugin-packs.md) Goal — workflow packs via `run(plugin=…)`, no new MCP tools.  
> **Handoff:** [`hld/handoff-plugin-packs-implementer.md`](handoff-plugin-packs-implementer.md)  
> **Next:** Commit and land the uncommitted work; confirm CI green; drive adoption with [`docs/packs.md`](../docs/packs.md). Optional: dokker adapter.
