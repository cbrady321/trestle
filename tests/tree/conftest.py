"""The tree band's fixtures (L.TR-0.6). `tests/conftest.py` and the root conftest stay untouched
(DM-18): everything tree-scoped lives here."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.proof import harness
from trestle.server.main import Kernel


@pytest.fixture
def tree_kernel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Kernel:
    """A kernel over a fresh `TRESTLE_HOME` (MC-26 `fresh_kernel`), one per test.

    The home is `<tmp_path>/home`, so no two tests share one, and `TRESTLE_HOME` names it for the
    test's own children. The plugin directory starts empty (`<tmp_path>/plugins`): a test copies in
    the fixtures or generated trees it needs, so no test sees another's plugins. Recovery is
    skipped (there is nothing to recover)."""
    home = tmp_path / "home"
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    monkeypatch.setenv("TRESTLE_HOME", str(home))
    return harness.fresh_kernel([plugins], home=home)
