"""Toolchain-adapter tests: the packs `tests/` directory (shared fakes) and `tests/conformance` (the
family case modules) are put on `sys.path` (the packs tests have no package markers), and the stub
`stub_mise` (`tests/fixtures/stubs/`) is loaded by absolute path: that directory is a fixture
directory, not a package, and a stub is never found by a PATH search (MC-13)."""

from __future__ import annotations

import sys
from pathlib import Path

_TESTS = Path(__file__).resolve().parent.parent
for _directory in (_TESTS, _TESTS / "conformance"):
    if str(_directory) not in sys.path:
        sys.path.insert(0, str(_directory))
