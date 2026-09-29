"""Container-adapter tests: the packs `tests/` directory (shared fakes) and `tests/conformance` (the
family case modules) are put on `sys.path`; the packs tests have no package markers."""

from __future__ import annotations

import sys
from pathlib import Path

_TESTS = Path(__file__).resolve().parent.parent
for directory in (_TESTS, _TESTS / "conformance"):
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
