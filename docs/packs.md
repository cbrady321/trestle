# Workflow packs — agent guide

**Audience:** coding agents running Docker / pytest / migration pipelines via Trestle MCP  
**Agent workflow:** [`docs/agents.md`](agents.md) · **Plugins:** [`plugins.md`](plugins.md)

---

## Install

The tool plugins are **bundled packs**: they ship in this repository under
`packages/trestle-pack-<name>/`, carry Trestle's version, and have owner `trestle`.

| Pack | Plugin | Library |
|------|--------|---------|
| `docker` | `docker_stack` | `trestle_pack_docker` |
| `pytest` | `pytest_run` | `trestle_pack_pytest` |
| `migrate` | `migrate_apply` | `trestle_pack_migrate` |
| `stack` | `stack_pipeline` | none: it requires the three above |

A fresh install adds none of them. The operator chooses, from a running service
([`install.md`](install.md)):

1. **Install:** `trestle pack install stack` installs `docker`, `pytest`, `migrate` and `stack`;
   `trestle pack install docker` installs one. Each pack is copied to
   `$TRESTLE_HOME/packs/trestle/<pack>/`, and its library is installed into the service's own
   Python from that copy. The service picks the packs up without a restart. `trestle pack list`
   shows what is installed and what else is available.

   The libraries need no Docker, pytest, Alembic or Yoyo package. The plugins run the `docker`
   CLI, and run pytest and migrations on **the project's own interpreter**, so those tools belong
   in the project's environment, not the service's.

2. **Give plugins the operator settings they read** in `$TRESTLE_HOME/config.toml`. The service
   passes nothing from your shell:

   ```toml
   [plugins.env]
   TRESTLE_DOCKER_PATH = "/usr/local/bin/docker"   # only if docker is not on the service's PATH
   APP_DATABASE_URL = "postgresql://app:secret@localhost/app"   # for migrate_apply's database_url_env
   ```

   The env pack reads more (`TRESTLE_IMAGE_*`, `TRESTLE_ENV_COMPOSE_FILE`; see
   [`environment.md`](environment.md)). `[plugins.env]` is read at start:
   `trestle service restart` after changing it.
3. **Check:** `list_plugins` lists `docker_stack`, `pytest_run`, `migrate_apply` and
   `stack_pipeline` as valid, each with `pack: "<pack>@<version> (trestle)"`.

**Upgrading from 0.5.2 or earlier.** The tool plugins used to be loose files in the checkout's
`examples/packs/`, listed in `[plugins] paths`. `scripts/install.sh` (through
`trestle service upgrade`, which runs `trestle init --upgrade`) installs `stack` and removes that
entry, so the plugins keep their names with no manual edit. If it cannot (the build has no bundled
packs, or `[plugins] paths` is not a plain array under `[plugins]`), it leaves `config.toml` as it
was and prints the command: `trestle pack install stack`.

If a pack's library is missing from the service's Python, `list_plugins` returns
`valid: false` on its plugins and a `catalog_hint` naming the operator's command
(`trestle pack install <pack>`), and `run` returns `admission.import_failed` (no `run_id`).

**Writing your own pack:** a folder with a `pack.toml`, wrapper files under `plugins/` that declare
the library with `@trestle(packages=["your_pack"])`, and the library as a normal Python
distribution (`library = "."` and a `pyproject.toml`). The operator installs it with
`trestle pack install <path>` (served in place; a library edit republishes with no restart) or
`trestle pack install <git-url>@<tag>`. A loose wrapper file in a `[plugins] paths` folder still
works too, with its library installed by hand. Declaring the library records its code at
publication and checks it at each run start (an undeclared import is published with a warning).
Unit-test the wrappers with `trestle.plugin.testing`
([`plugins.md`](plugins.md#testing-a-plugin-without-a-server)).

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
result shapes), behind each library's `__all__`: `trestle_pack_docker`, `trestle_pack_pytest`,
`trestle_pack_migrate`. Read the installed copy under `$TRESTLE_HOME/packs/trestle/<pack>/`
(`describe_plugin` gives the wrapper's `source_path`). To change a flag, add a step or judge
differently, copy the wrapper, edit it and publish it under a new name (a publish under the
bundled name is refused). Its tests (`tests/test_<plugin>.py` in the pack) copy along: they call
the wrapper with `FakeContext`, over the fake docker CLI (`trestle_pack_docker.fake_cli`).

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
python -m pytest -q packages/trestle-pack-docker/tests packages/trestle-pack-pytest/tests \
  packages/trestle-pack-migrate/tests packages/trestle-pack-stack/tests
```

Docker must be installed on the host for `docker_stack` and `stack_pipeline`. Live compose demos use `alpine:3.20` from the pack test fixture — pre-pull when offline:

```bash
docker pull alpine:3.20
```
