"""`migrate_apply` (the migrate pack), called with `FakeContext`: Alembic and Yoyo run out of
process on the interpreter given as the project's `python` (here this test interpreter), against
SQLite.

Copy this file beside an adapted wrapper to test it the same way."""

from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

import pytest
from migrate_pack_testing import PYTHON, context, load_wrapper

from trestle.plugin import Blocked
from trestle.plugin.testing import call_plugin

MIGRATIONS = Path(__file__).resolve().parent / "fixtures" / "yoyo-sqlite" / "migrations"
REVISION = '''"""create pack_demo"""
from alembic import op

revision = "0001"
down_revision = None


def upgrade():
    op.execute("CREATE TABLE pack_demo (id INTEGER PRIMARY KEY)")


def downgrade():
    op.execute("DROP TABLE pack_demo")
'''


def _migrate(tmp_path: Path, args: dict) -> dict:
    answer = call_plugin(
        load_wrapper("migrate_apply").migrate_apply,
        {"python": PYTHON, **args},
        ctx=context(tmp_path),
    )
    assert isinstance(answer, dict)
    return answer


def _tables(db: Path) -> list[str]:
    with sqlite3.connect(db) as conn:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return sorted(r[0] for r in rows)


def test_yoyo_applies_then_has_nothing_pending(tmp_path: Path) -> None:
    db = tmp_path / "demo.db"
    args = {"backend": "yoyo", "database_url": f"sqlite:///{db}", "migrations_dir": str(MIGRATIONS)}
    first = _migrate(tmp_path, args)
    assert first["ok"] is True and first["applied"] == 1, first
    assert "pack_demo" in _tables(db)
    second = _migrate(tmp_path, args)
    assert second["ok"] is True and second["applied"] == 0


def test_yoyo_url_from_an_operator_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "secret.db"
    monkeypatch.setenv("APP_DATABASE_URL", f"sqlite:///{db}")
    ctx = context(tmp_path)
    args = {
        "backend": "yoyo",
        "database_url_env": "APP_DATABASE_URL",
        "migrations_dir": str(MIGRATIONS),
        "python": PYTHON,
    }
    answer = call_plugin(load_wrapper("migrate_apply").migrate_apply, args, ctx=ctx)
    assert isinstance(answer, dict) and answer["ok"] is True and answer["applied"] == 1
    (call,) = ctx.exec_calls
    assert str(db) not in " ".join(call.argv)  # the URL is never on the command line
    assert call.argv[1] == "-c" and call.argv[3] == "APP_DATABASE_URL"
    monkeypatch.delenv("APP_DATABASE_URL")
    with pytest.raises(Blocked) as blocked:
        call_plugin(load_wrapper("migrate_apply").migrate_apply, args, ctx=context(tmp_path / "b"))
    assert blocked.value.code == "database_url_env_unset"


def test_yoyo_bad_input_is_an_answer(tmp_path: Path) -> None:
    missing = _migrate(tmp_path, {"backend": "yoyo", "database_url": "sqlite:///x.db"})
    assert missing["ok"] is False and "migrations_dir" in missing["error"]
    no_url = _migrate(tmp_path, {"backend": "yoyo", "migrations_dir": str(MIGRATIONS)})
    assert no_url["ok"] is False and "database_url_env" in no_url["error"]
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "0001_bad.sql").write_text("CREATE TABLE (;\n", encoding="utf-8")
    failed = _migrate(
        tmp_path,
        {
            "backend": "yoyo",
            "database_url": f"sqlite:///{tmp_path / 'b.db'}",
            "migrations_dir": str(broken),
        },
    )
    assert failed["ok"] is False and "syntax error" in failed["error"]


def _alembic_project(root: Path) -> Path:
    root.mkdir(parents=True)
    subprocess.run(
        [PYTHON, "-m", "alembic", "init", "migrations"], cwd=root, check=True, capture_output=True
    )
    ini = root / "alembic.ini"
    text = ini.read_text(encoding="utf-8")
    ini.write_text(
        text.replace("driver://user:pass@localhost/dbname", f"sqlite:///{root / 'app.db'}"),
        encoding="utf-8",
    )
    (root / "migrations" / "versions" / "0001_create.py").write_text(REVISION, encoding="utf-8")
    return ini


def test_alembic_upgrade_reports_revisions(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    _alembic_project(root)
    answer = _migrate(tmp_path, {"config": "alembic.ini", "workdir": str(root)})
    assert answer["ok"] is True, answer
    assert (answer["before"], answer["after"], answer["target"]) == (None, "0001", "head")
    assert "pack_demo" in _tables(root / "app.db")
    again = _migrate(tmp_path, {"config": str(root / "alembic.ini")})  # root from the config
    assert again["ok"] is True and again["before"] == again["after"] == "0001"


def test_alembic_failure_and_missing_config(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    _alembic_project(root)
    bad = _migrate(tmp_path, {"config": "alembic.ini", "workdir": str(root), "target": "9999"})
    assert bad["ok"] is False and "9999" in bad["error"]
    missing = _migrate(tmp_path, {"workdir": str(root)})
    assert missing["ok"] is False and "alembic.ini" in missing["error"]


def test_tool_missing_from_the_project_interpreter_is_blocked(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    _alembic_project(root)
    bare = tmp_path / "bare-python"
    bare.write_text('#!/bin/sh\necho "$0: No module named alembic" >&2\nexit 1\n')
    bare.chmod(0o755)
    with pytest.raises(Blocked) as blocked:
        _migrate(tmp_path, {"config": "alembic.ini", "workdir": str(root), "python": str(bare)})
    assert blocked.value.code == "alembic_missing"
