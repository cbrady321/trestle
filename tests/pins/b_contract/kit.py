"""Lane-B helpers: run one lane fixture plugin through today's kernel."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from tests.proof import harness, tolerances
from trestle.common.types import RunView
from trestle.server.ledger import run_dir_for

PLUGINS = Path(__file__).parent / "plugins"


def run_plugin(
    tmp_path: Path, name: str, args: dict[str, Any] | None = None
) -> tuple[RunView, Path]:
    """Copy `plugins/<name>.py` alone into a private plugin dir (so a kernel
    never sees the multi-callable fixtures), run it, return (view, run_dir)."""
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    shutil.copy2(PLUGINS / f"{name}.py", plugin_dir / f"{name}.py")
    kernel = harness.fresh_kernel([plugin_dir], home=tmp_path / "home")
    view = kernel.control.run(plugin=name, args=args or {}, wait_ms=tolerances.HARNESS_WAIT_MS)
    assert isinstance(view, RunView), view
    return view, run_dir_for(kernel.home, view.run_id)
