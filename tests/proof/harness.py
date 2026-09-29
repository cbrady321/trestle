"""Test harness over today's kernel (L.P0-0b.1; DM-07, DM-08).

Drives runs through the same seam an MCP client would (`ControlSurface`),
never around admission/conductor/project — a proof test that plants a
defect must see it the way a real client would.
"""

from __future__ import annotations

import dataclasses
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from trestle.common.types import RequestOutcome, RunView
from trestle.server.ledger import run_dir_for
from trestle.server.main import Kernel, create_kernel

DEFAULT_PLUGIN_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "plugins"


def fresh_kernel(
    plugin_dirs: list[Path] | None = None,
    *,
    home: Path | None = None,
) -> Kernel:
    """A kernel over a throwaway `TRESTLE_HOME`.

    Recovery is skipped: the directory this call creates has nothing to
    recover. Defaults to the shared fixture plugin directory
    (`tests/fixtures/plugins`) unless the caller names its own.
    """
    trestle_home = (
        home if home is not None else Path(tempfile.mkdtemp(prefix="trestle-proof-home-"))
    )
    trestle_home.mkdir(parents=True, exist_ok=True)
    dirs = plugin_dirs if plugin_dirs is not None else [DEFAULT_PLUGIN_DIR]
    return create_kernel(home=trestle_home, plugin_dirs=dirs, skip_recovery=True)


@contextmanager
def patch_snapshot(kernel: Kernel, name: str, **fields: Any) -> Iterator[None]:
    """Replace fields on plugin `name`'s registered snapshot for the life
    of the context (generalizes the `_patch_slow_timeout` idiom in
    `tests/test_m3_durability.py`), restoring the original on exit."""
    snap = kernel.registry.get(name)
    if snap is None:
        raise KeyError(f"no snapshot registered for plugin {name!r}")
    original = kernel.registry.snapshots[name]
    kernel.registry.snapshots[name] = dataclasses.replace(snap, **fields)
    try:
        yield
    finally:
        kernel.registry.snapshots[name] = original


def run_to_dir(
    kernel: Kernel,
    plugin: str,
    args: dict[str, Any] | None = None,
    *,
    wait_ms: int = 5000,
) -> Path:
    """Run `plugin` over `kernel.control.run` and return its run directory.

    Raises if admission refuses the run; does not itself assert the run
    reached a terminal state (a caller with a slow plugin may want to poll
    further — `records.node_record` reads whatever the ledger holds so
    far).
    """
    result = kernel.control.run(plugin=plugin, args=args or {}, wait_ms=wait_ms)
    if isinstance(result, RequestOutcome):
        raise RuntimeError(f"run refused: {result.code} {result.message}")
    assert isinstance(result, RunView)
    return run_dir_for(kernel.home, result.run_id)
