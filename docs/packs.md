# Workflow packs — agent guide

**Audience:** coding agents running Docker / pytest / migration pipelines via Trestle MCP  
**Agent workflow:** [`docs/agents.md`](agents.md) · **Plugins:** [`plugins.md`](plugins.md)

---

## Install

```bash
pip install -e ".[dev]"
pip install -e ".[packs]"          # or: pip install -e "packages/trestle-packs[all]"
trestle serve --plugin-dir examples/packs
```

If `trestle_packs` is missing, `list_plugins` returns `valid: false` on pack plugins, a `catalog_hint` with install instructions, and `run` returns `admission.import_failed` (no `run_id`).

---

## Plugins

| Plugin | Purpose |
|--------|---------|
| `docker_stack` | Dependency-ordered compose up with per-service log artifacts |
| `pytest_run` | In-process pytest with JSON report artifact |
| `migrate_apply` | Alembic or Yoyo migration apply |
| `integration_pipeline` | docker → migrate → pytest in one run |

Discover schemas with `describe_plugin(plugin_id="docker_stack")`.

---

## Docker stack spec

Copy-paste into `run(plugin="docker_stack", args={…})`:

```json
{
  "spec": {
    "compose_file": "./docker-compose.yml",
    "project": "myapp",
    "teardown": "down",
    "waves": [
      {
        "name": "data",
        "services": ["postgres", "redis"],
        "wait": "healthy",
        "timeout_s": 120
      },
      {
        "name": "app",
        "services": ["api", "worker"],
        "wait": "healthy",
        "timeout_s": 180
      }
    ],
    "depends_on": {
      "api": ["postgres", "redis"],
      "worker": ["postgres", "redis"]
    },
    "probes": {
      "postgres": {
        "kind": "command",
        "command": ["pg_isready", "-U", "postgres"]
      }
    }
  },
  "workdir": "/path/to/project"
}
```

### Wait modes

| Mode | Behavior |
|------|----------|
| `healthy` | `docker compose up --wait` (python-on-whales) |
| `started` | Start detached; no health wait |
| `custom` | Poll `probes[service].command` until success |

### Teardown

| Value | On failure or `down()` |
|-------|------------------------|
| `down` | `docker compose down` (removes containers + networks) |
| `stop` | `docker compose stop` (containers preserved) |
| `none` | No automatic cleanup |

### Evidence

After `await_runs`, grep setup logs:

```
query(run_artifacts) → fetch(art_…)   # per-service: postgres.log, api.log, …
query(run_events)                     # wave milestones: [docker] wave ready: data
```

---

## Pytest run

```json
{
  "plugin": "pytest_run",
  "args": {
    "path": "tests",
    "markers": "not slow",
    "keyword": "auth"
  },
  "wait_ms": 120000
}
```

On success: `fetch({run_id}/result)` for summary counts; `query(run_artifacts)` for `pytest-report.json`.

---

## Migration apply

**Alembic:**

```json
{
  "plugin": "migrate_apply",
  "args": {
    "backend": "alembic",
    "config": "alembic.ini",
    "target": "head"
  }
}
```

**Yoyo:**

```json
{
  "plugin": "migrate_apply",
  "args": {
    "backend": "yoyo",
    "database_url": "postgresql://user:pass@localhost/db",
    "migrations_dir": "migrations"
  }
}
```

---

## Integration pipeline

Full stack test in one `run`:

```json
{
  "plugin": "integration_pipeline",
  "args": {
    "stack_spec": {
      "compose_file": "./docker-compose.yml",
      "project": "integration",
      "teardown": "down",
      "waves": [
        {"name": "data", "services": ["postgres"], "wait": "healthy", "timeout_s": 120},
        {"name": "app", "services": ["api"], "wait": "healthy", "timeout_s": 180}
      ]
    },
    "alembic_config": "alembic.ini",
    "pytest_path": "tests/integration",
    "workdir": "/path/to/project"
  },
  "wait_ms": 0
}
```

Then `await_runs(timeout_ms=600000)`. On pytest failure the stack is torn down automatically (`teardown` policy from `stack_spec`).

---

## Agent workflow

```
list_plugins → describe_plugin? → run(plugin, wait_ms=0)
  → await_runs → query(run_events) → query(run_artifacts) → fetch(art_…)
```

Long Docker waves: use short `wait_ms` for `run_id`, then `await_runs`.

---

## Verify

```bash
python scripts/smoke_packs.py           # expect PACKS SMOKE OK (pytest + migrate)
python scripts/demo_pack_workflows.py   # all four plugins; docker steps skip if daemon down
pytest packages/trestle-packs/tests/ -q
```

Docker must be installed on the host for `docker_stack` and `integration_pipeline`. Live compose demos use `alpine:3.20` from the pack test fixture — pre-pull when offline:

```bash
docker pull alpine:3.20
```
