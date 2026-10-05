"""Yoyo migration runner."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class YoyoResult:
    database_url: str
    migrations_dir: str
    applied_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend": "yoyo",
            "database_url": self.database_url,
            "migrations_dir": self.migrations_dir,
            "applied_count": self.applied_count,
        }


def run_yoyo_apply(database_url: str, migrations_dir: str | Path) -> YoyoResult:
    from yoyo import get_backend, read_migrations

    mig_dir = Path(migrations_dir)
    if not mig_dir.is_dir():
        msg = f"migrations directory not found: {mig_dir}"
        raise FileNotFoundError(msg)

    backend = get_backend(database_url)
    migrations = read_migrations(str(mig_dir))
    to_apply = backend.to_apply(migrations)
    with backend.lock():
        backend.apply_migrations(to_apply)
    return YoyoResult(
        database_url=database_url,
        migrations_dir=str(mig_dir.resolve()),
        applied_count=len(to_apply),
    )
