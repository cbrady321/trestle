"""R3 acceptance: a pack's plugin (the shipped `examples/packs/pytest_run.py` wrapper) is unit
tested with `trestle.plugin.testing.FakeContext`, with no server."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from trestle.plugin.testing import FakeContext, call_plugin

EXAMPLE = Path(__file__).resolve().parents[3] / "examples" / "packs" / "pytest_run.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("example_pytest_run", EXAMPLE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_pytest_run_example_under_a_fake_context(tmp_path: Path) -> None:
    suite = tmp_path / "suite"
    suite.mkdir()
    (suite / "test_tiny.py").write_text(
        "def test_one():\n    assert True\n\ndef test_two():\n    assert 1 + 1 == 2\n",
        encoding="utf-8",
    )
    ctx = FakeContext(tmp_path / "ctx")
    answer = call_plugin(_load().pytest_run, {"path": str(suite)}, ctx=ctx)
    assert isinstance(answer, dict)
    assert answer["passed"] == 2 and answer["failed"] == 0
    assert "pytest-report.json" in ctx.artifacts
    assert ctx.logs[0].startswith("pytest: collecting from")
