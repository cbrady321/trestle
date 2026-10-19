# Workflow packs — agent guide

**Audience:** coding agents running Docker / pytest / migration pipelines via Trestle MCP  
**Agent workflow:** [`docs/agents.md`](agents.md) · **Plugins:** [`plugins.md`](plugins.md)

---

## Install

A pack is two things: a **library** (here the `trestle_packs` package) and a **folder of thin
`@trestle` wrappers** that call it (here `examples/packs/`). Trestle never loads plugins from a
package's entry points: the service publishes the wrapper files it finds in the folders listed
under `[plugins] paths`, the same way as any other plugin. From a running service
([`install.md`](install.md)):

1. **Install the library into the service's environment**, the one whose `trestle` binary the
   service unit runs (the `pip` beside that binary), not another virtualenv:

   ```bash
   pip install -e "packages/trestle-packs[all]"    # or: pip install -e ".[packs]"
   ```

2. **List the wrapper folder** in `$TRESTLE_HOME/config.toml`, by absolute path, keeping the
   folders already there (the default is `$TRESTLE_HOME/plugins`):

   ```toml
   [plugins]
   paths = ["~/.trestle/plugins", "/ABS/PATH/TO/trestle/examples/packs"]
   ```

3. **Give plugins the operator settings they read** (the env pack's `TRESTLE_DOCKER_PATH`,
   `TRESTLE_IMAGE_*`, `TRESTLE_ENV_COMPOSE_FILE`; see [`environment.md`](environment.md)) in the
   same file. The service passes nothing from your shell:

   ```toml
   [plugins.env]
   TRESTLE_DOCKER_PATH = "/usr/local/bin/docker"
   ```

4. **Restart:** `trestle service restart` (`[plugins] paths` is read at start).
5. **Check:** `list_plugins` lists `docker_stack`, `pytest_run`, `migrate_apply` and
   `integration_pipeline` as valid.

If `trestle_packs` is missing from the service's environment, `list_plugins` returns `valid: false`
on pack plugins, a `catalog_hint` with install instructions, and `run` returns
`admission.import_failed` (no `run_id`).

**Writing your own pack:** ship the library as a normal Python distribution and a folder of
wrapper files, each a few lines that import the library and declare it with
`@trestle(packages=["your_pack"])`, so its code is recorded at publication and checked at each
run start (an undeclared import is published with a warning). Unit-test the wrappers with
`trestle.plugin.testing` ([`plugins.md`](plugins.md#testing-a-plugin-without-a-server)). After
upgrading the library, the next run of a wrapper stops with `execution.provenance_mismatch` until
the wrapper is published again (`trestle service restart` republishes every watched file).

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

| Value | On failure or `down()` | Removes |
|-------|------------------------|---------|
| `down` | `docker compose down` (containers and networks; volumes are kept) | containers, networks |
| `stop` | `docker compose stop` (containers are stopped and kept) | nothing |
| `none` | No automatic cleanup | nothing |
| `reset_volumes=True` | `StackRunner.down(spec, reset_volumes=True)`: `docker compose down --volumes`, whatever the declared policy | containers, networks, volumes |

No default or failure-path teardown deletes a volume, the legacy pack's included. Removing volumes is
an explicit, non-default choice: `reset_volumes=True` is a Python-only argument of `StackRunner.down`,
and no entry-point schema (`docker_stack`, `integration_pipeline`) exposes it. Earlier releases removed
the stack's volumes on every `down` teardown; that changed (K-6), so state kept in named volumes now
survives a stack teardown.

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

Then `await_runs(timeout_ms=600000)`. The stack is stopped exactly once on every path (`teardown` policy from `stack_spec`; volumes are kept, see Teardown above):

| Path | Teardown |
|------|----------|
| `up` fails | `up` tears the stack down itself, once; the pipeline does not tear down again |
| migrate or pytest fails | once, after the failing stage |
| success | once, after pytest passes (earlier releases left the stack running) |

`teardown: none` means no effect on every path.

`integration_pipeline` is one plain plugin that runs its stages in sequence and reports one class. A declared tree plugin instead gives each part its own verdict in one answer, runs independent parts in parallel (`needs`, `concurrency`), and checks every part's time budget at admission, before a run id exists. For a large task with many parts, see [`agents.md` § Large tasks: the tree](agents.md#large-tasks-the-tree).

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
