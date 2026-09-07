"""Yoyo migration runner tests."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from trestle_packs.migrate.yoyo_runner import run_yoyo_apply

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "yoyo-sqlite"
MIGRATIONS_DIR = FIXTURE_DIR / "migrations"


def test_run_yoyo_apply_creates_table(tmp_path: Path) -> None:
    db_path = tmp_path / "demo.db"
    database_url = f"sqlite:///{db_path}"

    result = run_yoyo_apply(database_url, MIGRATIONS_DIR)

    assert result.applied_count == 1
    assert result.to_dict()["backend"] == "yoyo"
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='pack_demo'"
        ).fetchall()
        assert rows == [("pack_demo",)]
    finally:
        conn.close()


def test_run_yoyo_apply_idempotent_second_run(tmp_path: Path) -> None:
    db_path = tmp_path / "demo.db"
    database_url = f"sqlite:///{db_path}"

    first = run_yoyo_apply(database_url, MIGRATIONS_DIR)
    second = run_yoyo_apply(database_url, MIGRATIONS_DIR)

    assert first.applied_count == 1
    assert second.applied_count == 0


def test_run_yoyo_apply_missing_dir(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="migrations directory not found"):
        run_yoyo_apply(f"sqlite:///{tmp_path / 'x.db'}", tmp_path / "missing")
