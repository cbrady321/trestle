"""Legacy-pack tests: the packs `tests/` directory carries the shared fakes (no package markers)."""

from __future__ import annotations

import sys
from pathlib import Path

_TESTS = str(Path(__file__).resolve().parent.parent)
if _TESTS not in sys.path:
    sys.path.insert(0, _TESTS)
