# Plugin packs — design

Scope: trestle-packs  
Status: **Complete** (2026-08-29) — library, example plugins, integration tests, smoke, agent docs  
Parent: [`trestle-requirements.md`](../trestle-requirements.md) **Goals & Non-Goals**

## Goal

Pre-built **workflow packs** (Docker, pytest, migrations) that agents invoke via existing MCP `run(plugin=…)` — dependency ordering, readiness waits, and grep-able artifacts built in, without turning Trestle into a workflow engine.

## Guiding light

Agents call `run(plugin="docker_stack", args={spec: …})` and get `run_id` + artifacts. Authors extend by importing `trestle_packs.*` in thin `@trestle` wrappers, not by shell scripts.

## Constraints (inherited)

From [`trestle-requirements.md`](../trestle-requirements.md):

- **Not a workflow engine** — packs are optional libraries + thin plugins
- **PluginSurface only** in plugins — no Kernel / FastMCP / ledger imports (R-BOUND-2)
- **Ten MCP tools frozen** — no new porch tools (R-MCP-1)
- **Third-party deps** must be installed or plugins fail validation (R-PLUG-20)
- **Evidence via Context** — `ctx.log`, `ctx.attach`, small return dicts

## Architecture

Three seams (do not merge):

| Seam | Location | Consumer |
|------|----------|----------|
| **Pack library** | `packages/trestle-packs/trestle_packs/` | Plugin authors |
| **Default plugins** | `examples/packs/*.py` | Agents via `run` |
| **Declarative spec** | JSON in `args.spec` | Agents without Python |

### Package layout

```
packages/trestle-packs/
  trestle_packs/
    core/          # DAG waves, readiness poll, artifacts helpers
    docker/        # StackSpec, WhaleComposeBackend, StackRunner
    pytest/        # in-process collector + JSON report
    migrate/       # alembic + yoyo adapters
examples/packs/
  docker_stack.py
  pytest_run.py
  migrate_apply.py
  integration_pipeline.py
```

### Install

```bash
pip install -e ".[dev]"
pip install -e ".[packs]"          # or: pip install -e "packages/trestle-packs[all]"
trestle serve --plugin-dir examples/packs
```

Optional dependency groups: `docker`, `pytest`, `migrate`, `all`.

## Declarative Docker spec

```json
{
  "compose_file": "./docker-compose.yml",
  "project": "myapp",
  "teardown": "down",
  "waves": [
    {"name": "data", "services": ["postgres", "redis"], "wait": "healthy", "timeout_s": 120},
    {"name": "app", "services": ["api", "worker"], "wait": "healthy", "timeout_s": 180}
  ],
  "depends_on": {"api": ["postgres", "redis"]},
  "probes": {
    "postgres": {"kind": "command", "command": ["pg_isready", "-U", "postgres"]}
  }
}
```

### Wait modes

| Mode | Behavior |
|------|----------|
| `healthy` | `docker compose up --wait` via python-on-whales |
| `started` | Start detached; no health wait |
| `custom` | Poll `probes[service].command` until success |

## OSS dependencies

| Pack | Library | License | Role |
|------|---------|---------|------|
| Docker | [python-on-whales](https://github.com/gabrieldemarmiesse/python-on-whales) | Apache-2.0 | Primary compose backend |
| Docker (alt) | [dokker](https://pypi.org/project/dokker/) | — | Future richer health-check adapter |
| Pytest | pytest hooks | MIT | In-process `pytest.main` + collector |
| Migrate | Alembic `command.upgrade` | MIT | SQLAlchemy projects |
| Migrate | Yoyo `apply_migrations` | — | Simpler Python API |

DAG wave logic is **in-house** (`trestle_packs.core.dag`), inspired by [svcdag](https://pypi.org/project/svcdag/) patterns.

## Default plugins

| Plugin | Args | Stages |
|--------|------|--------|
| `docker_stack` | `spec` or `compose_file` | Wave orchestration |
| `pytest_run` | `path`, `markers`, `keyword` | Test + JSON artifact |
| `migrate_apply` | `backend`, `config` / `database_url` | Alembic or Yoyo |
| `integration_pipeline` | `stack_spec`, `alembic_config?`, `pytest_path` | docker → migrate → pytest |

## Agent workflow

```
list_plugins → run(docker_stack, wait_ms=0) → await_runs
  → query(run_events) → query(run_artifacts) → fetch(art_…, grep)
→ run(pytest_run) → fetch(result)
```

## Non-goals (v0.1 packs)

- Remote Docker / Kubernetes
- Live tail during orchestration
- New MCP tools per pack
- Operator HTTP `run` from browser

## Remaining work

None for v0.1. Optional future: dokker adapter (`trestle_packs.docker.dokker_adapter`).
