"""The github pack's tests: the library from this folder (it is not installed for the tests),
and helpers that load a wrapper from `plugins/` and give it a fake `gh`."""

from __future__ import annotations

import sys
from pathlib import Path

PACK = Path(__file__).resolve().parents[1]
if str(PACK) not in sys.path:
    sys.path.insert(0, str(PACK))
sys.path.insert(0, str(Path(__file__).resolve().parent))
