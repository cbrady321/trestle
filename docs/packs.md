# Workflow packs — agent guide

**Audience:** coding agents running Docker / pytest / migration pipelines via Trestle MCP  
**Agent workflow:** [`docs/agents.md`](agents.md) · **Plugins:** [`plugins.md`](plugins.md)

---

## Install

A pack is two things: a **library** (here the `trestle_packs` package) and a **folder of
`@trestle` wrappers** that use it (here `examples/packs/`). Trestle never loads plugins from a
package's entry points: the service publishes the wrapper files it finds in the folders listed
under `[plugins] paths`, the same way as any other plugin. From a running service
([`install.md`](install.md)):

1. **Install the library into the service's environment**, the one whose `trestle` binary the
   service unit runs (the `pip` beside that binary), not another virtualenv:

   ```bash
   pip install -e "packages/trestle-packs"    # or: pip install -e ".[packs]"
   ```

   The library needs no Docker, pytest, Alembic or Yoyo package. The plugins run the `docker`
   CLI, and run pytest and migrations on **the project's own interpreter**, so those tools belong
   in the project's environment, not the service's.

2. **List the wrapper folder** in `$TRESTLE_HOME/config.toml`, by absolute path, keeping the
   folders already there (the default is `$TRESTLE_HOME/plugins`):

   ```toml
   [plugins]
   paths = ["~/.trestle/plugins", "/ABS/PATH/TO/trestle/examples/packs"]
   ```

3. **Give plugins the operator settings they read** in the same file. The service passes nothing
   from your shell:

   ```toml
   [plugins.env]
   TRESTLE_DOCKER_PATH = "/usr/local/bin/docker"   # only if docker is not on the service's PATH
   APP_DATABASE_URL = "postgresql://app:secret@localhost/app"   # for migrate_apply's database_url_env
   ```

   The env pack reads more (`TRESTLE_IMAGE_*`, `TRESTLE_ENV_COMPOSE_FILE`; see
   [`environment.md`](environment.md)).

4. **Restart:** `trestle service restart` (`[plugins] paths` is read at start).
5. **Check:** `list_plugins` lists `docker_stack`, `pytest_run`, `migrate_apply` and
   `stack_pipeline` as valid.

If `trestle_packs` is missing from the service's environment, `list_plugins` returns `valid: false`
on pack plugins, a `catalog_hint` with install instructions, and `run` returns
`admission.import_failed` (no `run_id`).

**Writing your own pack:** ship the library as a normal Python distribution and a folder of
wrapper files that import it and declare it with `@trestle(packages=["your_pack"])`, so its code
is recorded at publication and checked at each run start (an undeclared import is published with
a warning). Unit-test the wrappers with `trestle.plugin.testing`
([`plugins.md`](plugins.md#testing-a-plugin-without-a-server)). After upgrading the library, the
next run of a wrapper stops with `execution.provenance_mismatch` until the wrapper is published
again (`trestle service restart` republishes every watched file).

---

## Plugins

| Plugin | Purpose |
|--------|---------|
| `docker_stack` | Compose up in dependency waves with per-service log artifacts; stop services; take the stack down |
| `pytest_run` | pytest on the project's interpreter; failing tests are a result |
| `migrate_apply` | Alembic or Yoyo, on the project's interpreter |
| `stack_pipeline` | docker → optional Alembic migrate → pytest → one teardown, in one run (was `integration_pipeline`) |

Discover schemas with `describe_plugin(plugin_id="docker_stack")`.

**Every answer has a top-level `ok`.** A stack that does not come up, a failing migration and a
failing test are all `ok: false` results with the failed step and the end of the tool's output;
the run itself still `succeeded`. A missing tool is `outcome: "blocked"` with the one action that
unblocks it:

| Code | When |
|------|------|
| `plugin.docker_missing` | No docker CLI (`TRESTLE_DOCKER_PATH`, PATH, the usual install places) |
| `plugin.docker_unreachable` | `docker info` does not answer: the engine is not running |
| `plugin.docker_compose_missing` | `docker compose` is not installed |
| `plugin.project_python_not_found` | No `.venv` or `venv` in the project (pass `python`) |
| `plugin.pytest_missing`, `plugin.alembic_missing`, `plugin.yoyo_missing` | The tool is not in the project's interpreter |
| `plugin.database_url_env_unset` | `database_url_env` names a variable that is not set |

**The project's interpreter** is `python` when given (absolute, or relative to `workdir`), else
the first of `.venv/bin/python`, `venv/bin/python` found in `workdir` or a parent up to the
repository root. Tools run with `PYTHONPATH` unset, so nothing of the service leaks in.

**Adapting a plugin.** Each wrapper is the whole recipe, top to bottom: every command as a visible
`ctx.exec` argv, the step order and the verdict. The library holds only the mechanism (finding
docker and the interpreter, wave planning, waits within the run's budget, reading JUnit XML,
result shapes), behind each module's `__all__`: `trestle_packs.docker`, `trestle_packs.pytest`,
`trestle_packs.migrate`. To change a flag, add a step or judge differently, copy the wrapper,
edit it and publish it under a new name. Its tests (`packages/trestle-packs/tests/test_<plugin>.py`)
copy along: they call the wrapper with `FakeContext`, over the fake docker CLI
(`trestle_packs.fakes.docker_cli`).

---

## Docker stack

Minimal form: every service of `docker-compose.yml` in `workdir`, in one wave, waiting until
healthy (or running, for services without a health check):

```json
{"plugin": "docker_stack", "args": {"workdir": "/path/to/project", "project": "myapp"}}
```

Full spec, copy-paste into `run(plugin="docker_stack", args={…})`:

```json
{
  "spec": {
    "project": "myapp",
    "compose_files": ["docker-compose.yml", "docker-compose.test.yml"],
    "profiles": ["test"],
    "env_files": [".env.test"],
    "build": true,
    "no_deps": false,
    "teardown": "down",
    "waves": [
      {"name": "data", "services": ["postgres", "redis"], "wait": "healthy", "timeout_s": 120},
      {"name": "app", "services": ["api", "worker"], "wait": "custom", "timeout_s": 180}
    ],
    "depends_on": {"api": ["postgres", "redis"], "worker": ["postgres", "redis"]},
    "probes": {
      "api": {"kind": "command", "command": ["curl", "-fsS", "http://localhost:8000/health"]},
      "worker": {"kind": "command", "command": ["true"]}
    }
  },
  "workdir": "/path/to/project"
}
```

- `compose_file` (one) or `compose_files` (several, in order), `profiles`, `env_files`: become
  `--file`, `--profile` and `--env-file`; paths are relative to `workdir`.
- `build`: `up --build`. `no_deps`: `up --no-deps` (start only the named services).
- Services in no declared wave start in waves planned from `depends_on`, waiting `spec.wait` up to
  `spec.timeout_s`.
- `services`: limit `up` to these services (and their declared waves).
- `action: "stop"` stops the stack or just `services`; `action: "down"` takes the stack down
  (containers and networks; volumes are kept).

### Wait modes

| Mode | Behavior |
|------|----------|
| `healthy` | `docker compose up --detach --wait --wait-timeout <timeout_s>` |
| `started` | `docker compose up --detach`; no health wait |
| `custom` | `docker compose up --detach`, then run `probes[service].command` on the host until it exits 0, within `timeout_s` and the run's remaining time |

### Teardown

What a failed `up` (and `stack_pipeline`, on every path) runs once:

| Value | Runs | Removes |
|-------|------|---------|
| `down` | `docker compose down` (containers and networks; volumes are kept) | containers, networks |
| `stop` | `docker compose stop` (containers are stopped and kept) | nothing |
| `none` | No automatic cleanup | nothing |

No plugin removes a volume: none passes `--volumes`, so state kept in named volumes survives
every teardown (K-6). Removing volumes is an operator's act (`docker compose down --volumes`).
Earlier releases also had a Python-only `StackRunner.down(reset_volumes=True)`; it is gone with
the python-on-whales backend.

### Evidence

```
query(run_artifacts) → fetch(art_…)   # per service: postgres.log, api.log, …; docker-up-<wave>.log
query(run_events)                     # wave milestones: [docker] wave ready: data
```

---

## Pytest run

```json
{
  "plugin": "pytest_run",
  "args": {
    "path": "tests",
    "workdir": "/path/to/project",
    "markers": "not slow",
    "keyword": "auth",
    "pytest_args": ["-x"],
    "timeout_s": 900
  },
  "wait_ms": 120000
}
```

The answer is the verdict: `ok`, `summary`, the counts (`passed`, `failed`, `errors`, `skipped`;
setup, teardown and collection errors count as `errors`), and up to `max_failures` (default 20)
`failures`, each with its `nodeid`, `message` and traceback `detail`. Counts come from pytest's
JUnit XML, never from console text. Artifacts: `pytest-report.json`, `pytest-junit.xml` and
`pytest-output.log`.

---

## Migration apply

**Alembic** (`before` and `after` name the revision):

```json
{
  "plugin": "migrate_apply",
  "args": {"backend": "alembic", "config": "alembic.ini", "target": "head", "workdir": "/path/to/project"}
}
```

**Yoyo** (`applied` counts the migrations run). Name an operator variable holding the URL, so a
password is never in a run's arguments; the URL reaches Yoyo in the environment, never on a
command line:

```json
{
  "plugin": "migrate_apply",
  "args": {
    "backend": "yoyo",
    "database_url_env": "APP_DATABASE_URL",
    "migrations_dir": "migrations",
    "workdir": "/path/to/project"
  }
}
```

`database_url` gives a URL directly, for one without a secret (`sqlite:///app.db`).

---

## Stack pipeline

Full stack test in one `run`:

```json
{
  "plugin": "stack_pipeline",
  "args": {
    "stack_spec": {
      "project": "integration",
      "compose_file": "docker-compose.yml",
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

Then `await_runs(timeout_ms=600000)`. The answer is `ok`, the `failed_stage` (`docker`,
`migrate` or `pytest`) and each stage's own answer under `stages`. The interpreter and docker are
checked first, so a `Blocked` answer never leaves a stack running. The stack is stopped exactly
once on every path (`teardown` policy from `stack_spec`; volumes are kept, see Teardown above):

| Path | Teardown |
|------|----------|
| `up` fails | `up` tears the stack down itself, once; the pipeline does not tear down again |
| migrate or pytest fails | once, after the failing stage |
| success | once, after pytest passes |

`teardown: none` means no effect on every path.

`stack_pipeline` is one plain plugin that runs its stages in sequence and reports one verdict. A declared tree plugin instead gives each part its own verdict in one answer, runs independent parts in parallel (`needs`, `concurrency`), and checks every part's time budget at admission, before a run id exists. For a large task with many parts, see [`agents.md` § Large tasks: the tree](agents.md#large-tasks-the-tree).

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

Docker must be installed on the host for `docker_stack` and `stack_pipeline`. Live compose demos use `alpine:3.20` from the pack test fixture — pre-pull when offline:

```bash
docker pull alpine:3.20
```
