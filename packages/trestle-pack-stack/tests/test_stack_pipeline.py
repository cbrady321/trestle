"""`stack_pipeline` (the stack pack), called with `FakeContext` over the fake docker CLI, with
its migrate and pytest stages run out of process on this test interpreter.

Copy this file beside an adapted wrapper to test it the same way. The teardown-once rule on
every path is `legacy/test_legacy_pipeline_teardown.py`."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from stack_pack_testing import PYTHON, STACK, compose, context, fake_docker, load_wrapper

from trestle.plugin import Blocked, Failed
from trestle.plugin._codec import encode
from trestle.plugin.testing import call_plugin

# K-3/K-4: these plugins take and return typed records (skipped if they are declined).
pytestmark = pytest.mark.typed_records

REVISION = """from alembic import op

revision = "0001"
down_revision = None


def upgrade():
    op.execute("CREATE TABLE seen (id INTEGER PRIMARY KEY)")
"""
TEST = (
    "import sqlite3, pathlib\n\n"
    "def test_migrated():\n"
    "    db = pathlib.Path(__file__).parents[1] / 'app.db'\n"
    '    names = sqlite3.connect(db).execute("SELECT name FROM sqlite_master").fetchall()\n'
    "    assert ('seen',) in names\n"
)
SPEC = {
    "project": "shop",
    "profiles": ["debug"],
    "waves": [
        {"name": "data", "services": ["postgres", "redis"], "wait": "healthy", "timeout_s": 30},
        {"name": "app", "services": ["api"], "wait": "started"},
    ],
}


def _project(root: Path, *, migrate: bool) -> None:
    compose(root, STACK)
    (root / "tests").mkdir()
    if migrate:
        subprocess.run([PYTHON, "-m", "alembic", "init", "migrations"], cwd=root, check=True,
                       capture_output=True)  # fmt: skip
        ini = root / "alembic.ini"
        text = ini.read_text(encoding="utf-8").replace(
            "driver://user:pass@localhost/dbname", f"sqlite:///{root / 'app.db'}"
        )
        ini.write_text(text, encoding="utf-8")
        (root / "migrations" / "versions" / "0001.py").write_text(REVISION, encoding="utf-8")
        (root / "tests" / "test_db.py").write_text(TEST, encoding="utf-8")
    else:
        (root / "tests" / "test_ok.py").write_text("def test_ok():\n    pass\n", encoding="utf-8")


def test_stack_migrate_pytest_then_one_teardown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "proj"
    _project(root, migrate=True)
    fake = fake_docker(tmp_path, monkeypatch)
    ctx = context(tmp_path)
    args = {
        "stack_spec": SPEC,
        "alembic_config": "alembic.ini",
        "workdir": str(root),
        "python": PYTHON,
    }
    answer = call_plugin(load_wrapper("stack_pipeline").stack_pipeline, args, ctx=ctx)
    assert isinstance(answer, dict) and answer["ok"] is True, answer
    assert answer["docker"]["waves_completed"] == ["data", "app"]
    assert answer["teardown"] == "down"
    assert answer["migrate"]["ok"] is True
    assert answer["pytest"]["passed"] == 1
    ups = [c for c in fake.calls if c.startswith("up ")]
    assert ups == ["up --detach --wait --wait-timeout 30 postgres redis", "up --detach api"]
    assert fake.calls[-1] == "down"
    assert "--profile" in next(c.argv for c in ctx.exec_calls if "up" in c.argv)
    assert {"pytest-junit.xml", "alembic-upgrade.log", "api.log"} <= set(ctx.artifacts)


def test_failed_migration_skips_pytest_and_still_tears_down(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "proj"
    _project(root, migrate=True)
    (root / "migrations" / "versions" / "0001.py").write_text(
        REVISION.replace("CREATE TABLE seen", "CREATE TABL seen"), encoding="utf-8"
    )
    fake = fake_docker(tmp_path, monkeypatch)
    args = {
        "stack_spec": SPEC,
        "alembic_config": "alembic.ini",
        "workdir": str(root),
        "python": PYTHON,
    }
    with pytest.raises(Failed) as failed:
        call_plugin(load_wrapper("stack_pipeline").stack_pipeline, args, ctx=context(tmp_path))
    assert failed.value.code == "migrate_failed" and "syntax error" in failed.value.detail
    answer = json.loads(encode(failed.value.result))
    assert (answer["ok"], answer["failed_stage"]) == (False, "migrate")
    assert answer["pytest"] is None and "syntax error" in answer["migrate"]["error"]
    assert answer["teardown"] == "down"
    assert fake.calls[-1] == "down"


def test_blocked_before_anything_starts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "proj"
    _project(root, migrate=False)
    (root / ".git").mkdir()
    fake = fake_docker(tmp_path, monkeypatch)
    args = {"stack_spec": SPEC, "workdir": str(root)}  # no python, no venv
    with pytest.raises(Blocked) as blocked:
        call_plugin(load_wrapper("stack_pipeline").stack_pipeline, args, ctx=context(tmp_path))
    assert blocked.value.code == "project_python_not_found"
    fake.set(engine_down=True)
    with pytest.raises(Blocked) as blocked:
        call_plugin(
            load_wrapper("stack_pipeline").stack_pipeline,
            {**args, "python": PYTHON},
            ctx=context(tmp_path / "b"),
        )
    assert blocked.value.code == "docker_unreachable"
    assert not [c for c in fake.calls if c.startswith("up")]
