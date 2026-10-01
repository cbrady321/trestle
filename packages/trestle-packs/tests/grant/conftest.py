"""Grant-adapter tests: the packs `tests/` directory (shared fakes) and `tests/conformance` (the
family case modules) are put on `sys.path` (the packs tests have no package markers), and the two
demo stubs (`tests/fixtures/stubs/stub_issuer.py`, `stub_cloud.py`) are loaded by absolute path:
that directory is a fixture directory, not a package, and a stub is never found by a PATH search."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

_TESTS = Path(__file__).resolve().parent.parent
for _directory in (_TESTS, _TESTS / "conformance"):
    if str(_directory) not in sys.path:
        sys.path.insert(0, str(_directory))

REPO = Path(__file__).resolve().parents[4]
STUBS = REPO / "tests" / "fixtures" / "stubs"


def load_stub(name: str) -> ModuleType:
    """The stub module `tests/fixtures/stubs/<name>.py`, loaded once by absolute path."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, STUBS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
