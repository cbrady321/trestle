"""Shared by the pytest pack's tests: load the wrapper from `plugins/`; pytest runs out of process
on this test interpreter, passed as the project's `python`.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

from trestle.plugin.testing import FakeContext

PLUGINS = Path(__file__).resolve().parents[1] / "plugins"
PYTHON = sys.executable  # passed to the wrappers as the project's interpreter


def load_wrapper(name: str) -> ModuleType:
    """The wrapper module `plugins/<name>.py` of this pack, freshly loaded."""
    spec = importlib.util.spec_from_file_location(f"wrapper_{name}", PLUGINS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclass annotations resolve through sys.modules
    spec.loader.exec_module(module)
    return module


def context(tmp_path: Path) -> FakeContext:
    return FakeContext(tmp_path / "ctx")
