"""Subprocess driver for G-E3 (run as a script; prints one JSON line).

Modes:
  run_pytest_setup_error <dir>      pytest_run on a dir whose only test errors at setup
  pipeline_fail | pipeline_ok <dir> stack_pipeline over the fake docker CLI (never an engine)
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path
from types import ModuleType
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

SETUP_ERROR_TEST = (
    "import pytest\n\n\n"
    "@pytest.fixture\n"
    "def broken():\n"
    "    raise RuntimeError('setup boom')\n\n\n"
    "def test_uses_broken(broken):\n"
    "    pass\n"
)
PASSING_TEST = "def test_ok():\n    pass\n"
COMPOSE = "services:\n  web:\n    image: alpine:3.20\n"


def _wrapper(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        f"{name}_under_test",
        ROOT / "packages" / f"trestle-pack-{name.split('_')[0]}" / "plugins" / f"{name}.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _run_pytest_setup_error(work: Path) -> dict:
    from trestle.plugin.testing import FakeContext

    tests = work / "tests"
    tests.mkdir()
    (tests / "test_setup_error.py").write_text(SETUP_ERROR_TEST)
    args = {"path": str(tests), "workdir": str(work), "python": sys.executable}
    return _answer(_wrapper("pytest_run").pytest_run, args, FakeContext(work / "ctx"))


def _pipeline(work: Path, *, fail_up: bool) -> dict:
    from trestle_pack_docker.fake_cli import FakeDocker, write_fake_docker

    from trestle.plugin.testing import FakeContext

    fake = FakeDocker(work / "docker.json")
    fake.set(fail=["web"] if fail_up else [])
    os.environ["TRESTLE_DOCKER_PATH"] = str(write_fake_docker(work / "bin", fake.state_path))
    (work / "docker-compose.yml").write_text(COMPOSE)
    tests = work / "tests"
    tests.mkdir()
    (tests / "test_ok.py").write_text(PASSING_TEST)
    args = {
        "stack_spec": {
            "project": "g-e3-fake",
            "waves": [{"name": "app", "services": ["web"], "wait": "started"}],
        },
        "workdir": str(work),
        "python": sys.executable,
    }
    result = _answer(_wrapper("stack_pipeline").stack_pipeline, args, FakeContext(work / "ctx"))
    calls = [c.split()[0] for c in fake.calls]
    return {"ok": result["ok"], "failed_stage": result["failed_stage"], "calls": calls,
            "down": calls.count("down")}  # fmt: skip


def _answer(fn: Any, args: dict, ctx: Any) -> dict:
    """The plugin's result as its run records it: returned, or carried by a raised `Failed`."""
    from trestle.plugin import Failed
    from trestle.plugin._codec import encode
    from trestle.plugin.testing import call_plugin

    try:
        result = call_plugin(fn, args, ctx=ctx)
    except Failed as failed:
        result = json.loads(encode(failed.result))
    assert isinstance(result, dict)
    return result


def main() -> None:
    mode = sys.argv[1]
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        if mode == "run_pytest_setup_error":
            out = _run_pytest_setup_error(work)
        elif mode in ("pipeline_fail", "pipeline_ok"):
            out = _pipeline(work, fail_up=mode == "pipeline_fail")
        else:
            raise SystemExit(f"unknown mode {mode}")
    sys.stdout.write("\nRESULT " + json.dumps(out) + "\n")


if __name__ == "__main__":
    main()
