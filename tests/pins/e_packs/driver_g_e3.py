"""Subprocess driver for G-E3 (run as a script; prints one JSON line).

Modes:
  run_pytest_setup_error <dir>      run_pytest on a dir whose only test errors at setup
  pipeline_fail | pipeline_ok <dir> integration_pipeline with the fake backend
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

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


class _Ctx:
    def __init__(self, work: Path) -> None:
        self.outputs = work / "outputs"
        self.outputs.mkdir(parents=True, exist_ok=True)
        self.work = work
        self.logs: list[str] = []

    def log(self, message: str) -> None:
        self.logs.append(message)

    def progress(self, message: str, *, fraction: float | None = None) -> None:
        self.logs.append(message)

    def artifact(self, name: str) -> Path:
        path = self.work / "artifact-staging" / f"{name}.partial"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def attach(self, path: Path, *, name: str) -> str:
        return f"art_{name}"


def _run_pytest_setup_error(work: Path) -> dict:
    from trestle_packs.pytest.runner import PytestSpec, run_pytest

    tests = work / "tests"
    tests.mkdir()
    (tests / "test_setup_error.py").write_text(SETUP_ERROR_TEST)
    result = run_pytest(PytestSpec(path=str(tests)), report_dir=work / "report")
    return result.to_dict()


def _pipeline(work: Path, *, fail_up: bool) -> dict:
    import trestle_packs.docker.runner as runner_mod
    from fake_backend import FakeComposeBackend

    backends: list[FakeComposeBackend] = []

    def factory() -> FakeComposeBackend:
        backend = FakeComposeBackend(fail_up=fail_up)
        backends.append(backend)
        return backend

    runner_mod.WhaleComposeBackend = factory  # type: ignore[assignment]

    spec = importlib.util.spec_from_file_location(
        "integration_pipeline_under_test", ROOT / "examples" / "packs" / "integration_pipeline.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    (work / "docker-compose.yml").write_text(COMPOSE)
    tests = work / "tests"
    tests.mkdir()
    (tests / "test_ok.py").write_text(PASSING_TEST)

    stack = module.PipelineStack(
        project="g-e3-fake",
        waves=[module.PipelineWave(name="app", services=["web"], wait="started")],
    )
    raised = ""
    try:
        module.integration_pipeline(_Ctx(work), stack, workdir=str(work))
    except Exception as exc:  # noqa: BLE001
        raised = f"{type(exc).__name__}: {exc}"
    calls = [c for b in backends for c in b.calls]
    return {"raised": raised, "calls": calls, "down": calls.count("down")}


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
