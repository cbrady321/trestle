"""migrate_apply: apply pending database migrations with Alembic or Yoyo, on the project's
own interpreter.

The recipe, top to bottom (adapt it by copying this file and publishing it under a new name):

1. find the project's interpreter (`python`, else `.venv` or `venv` in `workdir` or a parent up
   to the repository root; `Blocked` when there is none);
2. Alembic: `<python> -m alembic -c <config> current`, then `... upgrade <target>`, then
   `... current` again, so the answer names the revision before and after;
3. Yoyo: `<python> -c YOYO_APPLY <url variable> <migrations dir>`, the short script below, which
   prints how many migrations it applied. The database URL reaches it in an environment
   variable, never on the command line: name an operator variable with `database_url_env` (set
   under `[plugins.env]`) for a URL that holds a password.

Each command runs with `ctx.exec`, in `workdir`, with PYTHONPATH unset. A failed migration raises
`Failed("migration_failed", ...)` carrying the `MigrateResult` (`ok: false`, the end of the tool's
output in `error`); a request that cannot run at all (no config, no migrations folder, no
database URL) raises `Failed("migration_not_run", ...)`.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

from trestle_pack_migrate import (
    MigrateResult,
    alembic_revision,
    find_project_python,
    resolve_under,
    tail,
)

from trestle.plugin import Blocked, Context, Failed, trestle

YOYO_APPLY = """\
import json, os, sys
from yoyo import get_backend, read_migrations
backend = get_backend(os.environ[sys.argv[1]])
migrations = read_migrations(sys.argv[2])
with backend.lock():
    pending = backend.to_apply(migrations)
    backend.apply_migrations(pending)
print(json.dumps({"applied": len(pending)}))
"""
URL_VARIABLE = "TRESTLE_MIGRATE_DATABASE_URL"
NO_ENV = {"PYTHONPATH": None}


def missing_tool(stderr: str, tool: str, package: str, interpreter: str) -> None:
    if f"No module named {tool}" in stderr:
        raise Blocked(
            f"{tool}_missing",
            f"{tool} is not installed in the project's interpreter {interpreter}. Install it "
            f"(`{interpreter} -m pip install {package}`), then resend.",
        )


def not_run(result: MigrateResult, error: str) -> Failed:
    """The `Failed` for a request that cannot run: nothing was applied."""
    result.error = error
    return Failed("migration_not_run", error, result)


@trestle(
    packages=["trestle_pack_migrate"], summary_fields=["ok", "before", "after", "applied", "error"]
)
def migrate_apply(
    ctx: Context,
    backend: Literal["alembic", "yoyo"] = "alembic",
    config: str | None = None,
    target: str = "head",
    database_url: str | None = None,
    database_url_env: str | None = None,
    migrations_dir: str | None = None,
    workdir: str | None = None,
    python: str | None = None,
) -> MigrateResult:
    """Apply pending migrations out of process, on the project's interpreter.

    Alembic needs `config` (the alembic.ini path, relative to `workdir`) and upgrades to
    `target`. Yoyo needs `migrations_dir` and the database URL: `database_url_env` names an
    environment variable that holds it (use this for any URL with a password), or
    `database_url` gives it directly. `workdir` is the project root (default: the config's
    folder for an absolute `config`, else the current directory). A failed upgrade raises
    `Failed` (`migration_failed`) with the result (`ok: false`, `error`); a request missing
    its config, migrations folder or URL raises `Failed` (`migration_not_run`).
    """
    if config and Path(config).is_absolute() and not workdir:
        root = Path(config).parent
    else:
        root = Path(workdir) if workdir else Path.cwd()
    interpreter = find_project_python(root, python)
    result = MigrateResult(ok=False, backend=backend, python=interpreter)
    ctx.log(f"migrate: backend={backend} in {root} on {interpreter}")

    if backend == "alembic":
        result.target = target
        if not config or not resolve_under(root, config).is_file():
            raise not_run(
                result, f"alembic needs config=<path to alembic.ini>; not found: {config!r}"
            )
        alembic = [interpreter, "-m", "alembic", "-c", str(resolve_under(root, config))]
        before = ctx.exec([*alembic, "current"], cwd=root, timeout=120, env=NO_ENV, output=None)
        missing_tool(before.stderr, "alembic", "alembic", interpreter)
        upgrade = ctx.exec(
            [*alembic, "upgrade", target], cwd=root, env=NO_ENV, output="alembic-upgrade.log"
        )
        after = ctx.exec([*alembic, "current"], cwd=root, timeout=120, env=NO_ENV, output=None)
        result.before = alembic_revision(before.stdout) if before.ok else None
        result.after = alembic_revision(after.stdout) if after.ok else None
        result.ok = upgrade.ok
        result.error = None if upgrade.ok else tail(upgrade.stderr or upgrade.stdout)
    else:
        if not migrations_dir or not resolve_under(root, migrations_dir).is_dir():
            raise not_run(
                result, f"yoyo needs migrations_dir=<folder>; not found: {migrations_dir!r}"
            )
        if database_url_env:
            if not os.environ.get(database_url_env):
                raise Blocked(
                    "database_url_env_unset",
                    f"{database_url_env} is not set for plugins. Set it to the database URL "
                    "under [plugins.env] in $TRESTLE_HOME/config.toml, then resend.",
                )
            variable, env = database_url_env, NO_ENV
        elif database_url:
            variable, env = URL_VARIABLE, {**NO_ENV, URL_VARIABLE: database_url}
        else:
            raise not_run(result, "yoyo needs database_url_env (or database_url)")
        argv = [interpreter, "-c", YOYO_APPLY, variable, str(resolve_under(root, migrations_dir))]
        done = ctx.exec(argv, cwd=root, env=env, output="yoyo-apply.log")
        missing_tool(done.stderr, "yoyo", "yoyo-migrations", interpreter)
        result.ok = done.ok
        if done.ok:
            result.applied = int(json.loads(done.stdout.strip().splitlines()[-1])["applied"])
        else:
            result.error = tail(done.stderr or done.stdout)

    ctx.log(f"migrate: {'complete' if result.ok else 'failed'} ({backend})")
    if not result.ok:
        raise Failed(
            "migration_failed",
            f"{backend} upgrade failed: {tail(result.error or 'no output', 300)}",
            result,
        )
    return result
