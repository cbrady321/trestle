"""`pytest_run` (the pytest pack), called with `FakeContext`: pytest runs out of process on the
interpreter given as the project's `python` (here this test interpreter).

Copy this file beside an adapted wrapper to test it the same way."""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path

import pytest
from pytest_pack_testing import PYTHON, context, load_wrapper
from trestle_pack_pytest.junit import PytestReport

from trestle.plugin import Blocked, Failed
from trestle.plugin.testing import call_plugin

# K-3/K-4: these plugins take and return typed records (skipped if they are declined).
pytestmark = pytest.mark.typed_records

PASSING = "def test_one():\n    assert True\n\ndef test_two():\n    assert 1 + 1 == 2\n"
FAILING = (
    "import pytest\n\n"
    "class TestMath:\n"
    "    def test_wrong(self):\n"
    "        assert 1 + 1 == 3, 'arithmetic broke'\n\n"
    "@pytest.mark.slow\n"
    "def test_slow():\n"
    "    pass\n\n"
    "def test_fine():\n"
    "    pass\n"
)


def _suite(root: Path, **files: str) -> Path:
    (root / "tests").mkdir(parents=True, exist_ok=True)
    for name, body in files.items():
        (root / "tests" / f"{name}.py").write_text(body, encoding="utf-8")
    return root


def _run(tmp_path: Path, args: dict) -> tuple[dict, object]:
    ctx = context(tmp_path)
    answer = call_plugin(load_wrapper("pytest_run").pytest_run, args, ctx=ctx)
    assert isinstance(answer, dict)
    return answer, ctx


def _fails(tmp_path: Path, args: dict) -> tuple[Failed, dict]:
    """Run a suite that must fail: the `Failed` it raised and its result as a dict."""
    with pytest.raises(Failed) as raised:
        _run(tmp_path, args)
    result = raised.value.result
    assert isinstance(result, PytestReport)
    return raised.value, asdict(result)


def test_passing_suite(tmp_path: Path) -> None:
    root = _suite(tmp_path / "proj", test_tiny=PASSING)
    answer, ctx = _run(tmp_path, {"path": "tests", "workdir": str(root), "python": PYTHON})
    assert answer["ok"] is True and answer["exit_code"] == 0
    assert (answer["collected"], answer["passed"], answer["failed"]) == (2, 2, 0)
    assert answer["summary"].startswith("2 passed, 0 failed")
    assert answer["python"] == PYTHON
    assert {"pytest-report.json", "pytest-junit.xml", "pytest-output.log"} <= set(ctx.artifacts)
    report = json.loads(ctx.artifacts["pytest-report.json"])
    assert report["passed"] == 2


def test_failing_test_raises_failed_carrying_the_report(tmp_path: Path) -> None:
    root = _suite(tmp_path / "proj", test_math=FAILING)
    failed, answer = _fails(tmp_path, {"path": "tests", "workdir": str(root), "python": PYTHON})
    assert failed.code == "tests_failed" and failed.detail == "1 of 3 tests failed"
    assert answer["ok"] is False and answer["exit_code"] == 1
    assert (answer["passed"], answer["failed"], answer["errors"]) == (2, 1, 0)
    (failure,) = answer["failures"]
    assert failure["nodeid"] == "tests/test_math.py::TestMath::test_wrong"
    assert failure["kind"] == "failed"
    assert "arithmetic broke" in failure["message"]
    assert "assert 1 + 1 == 3" in failure["detail"]


def test_markers_keyword_and_args_reach_pytest(tmp_path: Path) -> None:
    root = _suite(tmp_path / "proj", test_math=FAILING)
    args = {
        "path": "tests",
        "workdir": str(root),
        "python": PYTHON,
        "markers": "not slow",
        "keyword": "fine or wrong",
        "pytest_args": ["-W", "ignore::pytest.PytestUnknownMarkWarning"],
    }
    ctx = context(tmp_path)
    with pytest.raises(Failed) as raised:  # the selected `wrong` test still fails
        call_plugin(load_wrapper("pytest_run").pytest_run, args, ctx=ctx)
    answer = asdict(raised.value.result)
    (call,) = ctx.exec_calls
    assert call.argv[:4] == (PYTHON, "-m", "pytest", "tests")
    assert ("-m", "not slow") == call.argv[call.argv.index("-m", 3) : call.argv.index("-m", 3) + 2]
    assert "-k" in call.argv and call.argv[-2:] == ("-W", "ignore::pytest.PytestUnknownMarkWarning")
    assert call.kwargs["cwd"] == root and call.kwargs["env"] == {"PYTHONPATH": None}
    assert (answer["passed"], answer["failed"], answer["skipped"]) == (1, 1, 0)


def test_failures_are_bounded(tmp_path: Path) -> None:
    many = "".join(f"def test_{i}():\n    assert False\n" for i in range(5))
    root = _suite(tmp_path / "proj", test_many=many)
    args = {"path": "tests", "workdir": str(root), "python": PYTHON, "max_failures": 2}
    failed, answer = _fails(tmp_path, args)
    assert failed.code == "tests_failed" and failed.detail == "5 of 5 tests failed"
    assert answer["failed"] == 5 and len(answer["failures"]) == 2
    assert answer["failures_omitted"] == 3


def test_no_tests_is_not_ok(tmp_path: Path) -> None:
    root = _suite(tmp_path / "proj", test_empty="")
    failed, answer = _fails(tmp_path, {"path": "tests", "workdir": str(root), "python": PYTHON})
    assert failed.code == "no_tests_collected"
    assert answer["ok"] is False and answer["exit_code"] == 5
    assert "no tests collected" in answer["summary"]


def test_project_venv_is_found(tmp_path: Path) -> None:
    root = _suite(tmp_path / "proj", test_tiny=PASSING)
    (root / ".git").mkdir()
    (root / ".venv" / "bin").mkdir(parents=True)
    os.symlink(PYTHON, root / ".venv" / "bin" / "python")
    sub = root / "tests"
    answer, _ = _run(tmp_path, {"path": ".", "workdir": str(sub)})
    assert answer["ok"] is True and answer["python"] == str(root / ".venv" / "bin" / "python")


def test_no_project_interpreter_is_blocked(tmp_path: Path) -> None:
    root = _suite(tmp_path / "proj", test_tiny=PASSING)
    (root / ".git").mkdir()
    with pytest.raises(Blocked) as blocked:
        _run(tmp_path, {"path": "tests", "workdir": str(root)})
    assert blocked.value.code == "project_python_not_found"
    assert "python=" in blocked.value.human_action


def test_pytest_missing_is_blocked(tmp_path: Path) -> None:
    root = _suite(tmp_path / "proj", test_tiny=PASSING)
    bare = tmp_path / "bare-python"
    bare.write_text('#!/bin/sh\necho "$0: No module named pytest" >&2\nexit 1\n')
    bare.chmod(0o755)
    with pytest.raises(Blocked) as blocked:
        _run(tmp_path, {"path": "tests", "workdir": str(root), "python": str(bare)})
    assert blocked.value.code == "pytest_missing"


def test_timeout_raises_failed_with_the_report(tmp_path: Path) -> None:
    root = _suite(
        tmp_path / "proj", test_sleep="import time\n\ndef test_s():\n    time.sleep(30)\n"
    )
    args = {"path": "tests", "workdir": str(root), "python": PYTHON, "timeout_s": 2}
    failed, answer = _fails(tmp_path, args)
    assert failed.code == "tests_incomplete" and "timed out" in failed.detail
    assert answer["ok"] is False and answer["exit_code"] is None
    assert answer["error"].startswith("timed out")
