"""The migrate pack's library: the mechanism behind `migrate_apply` (P-8).

The wrapper builds the `<python> -m alembic` or `<python> -c <yoyo script>` command line and runs
it with `ctx.exec` on the project's interpreter; this library finds that interpreter, reads
Alembic's revision lines and holds the result shape. Everything an adapted wrapper may rely on is
in `__all__`.
"""

from __future__ import annotations

from dataclasses import dataclass

from trestle_packs.core.interpreter import VENV_PYTHONS, find_project_python
from trestle_packs.core.paths import resolve_under
from trestle_packs.core.text import tail


@dataclass
class MigrateResult:
    """One migration run's verdict. `before` and `after` are the database's revision (Alembic)
    around the upgrade; `applied` is how many migrations ran (Yoyo). On a failure `error` quotes
    the end of the tool's output."""

    ok: bool
    backend: str
    target: str | None = None
    before: str | None = None
    after: str | None = None
    applied: int | None = None
    error: str | None = None
    python: str = ""


def alembic_revision(stdout: str) -> str | None:
    """The revision `alembic current` printed (`abc123 (head)` -> `abc123`), or None when the
    database has none. Alembic's `INFO` lines are skipped."""
    lines = [ln.strip() for ln in stdout.splitlines() if ln.strip()]
    lines = [ln for ln in lines if not ln.startswith(("INFO", "WARNING"))]
    return lines[-1].split()[0] if lines else None


__all__ = [
    "VENV_PYTHONS",
    "MigrateResult",
    "alembic_revision",
    "find_project_python",
    "resolve_under",
    "tail",
]
