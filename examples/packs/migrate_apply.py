"""Database migration plugin — Alembic or Yoyo."""

from __future__ import annotations

from trestle_packs.migrate.alembic_runner import AlembicResult, run_alembic_upgrade
from trestle_packs.migrate.yoyo_runner import YoyoResult, run_yoyo_apply

from trestle.plugin.surface import Context, trestle


@trestle
def migrate_apply(
    ctx: Context,
    backend: str = "alembic",
    config: str | None = None,
    target: str = "head",
    database_url: str | None = None,
    migrations_dir: str | None = None,
) -> AlembicResult | YoyoResult:
    """Apply pending database migrations."""
    ctx.log(f"migrate: backend={backend}")
    if backend == "alembic":
        if not config:
            msg = "alembic backend requires config path to alembic.ini"
            raise ValueError(msg)
        result = run_alembic_upgrade(config, target=target)
    elif backend == "yoyo":
        if not database_url or not migrations_dir:
            msg = "yoyo backend requires database_url and migrations_dir"
            raise ValueError(msg)
        result = run_yoyo_apply(database_url, migrations_dir)
    else:
        msg = f"unknown migration backend: {backend}"
        raise ValueError(msg)

    ctx.log(f"migrate: complete ({backend})")
    return result.to_dict()
