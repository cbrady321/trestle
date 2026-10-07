"""G-C3 (BFD-26), flipped by L.CK-1.1: the same idempotency key after a source edit joins the
run it named instead of being refused `IDEMPOTENCY_KEY_CONFLICT` (K-1 recorded default,
`admission.JOIN_ACROSS_REPUBLISH`). The S0 pin is deleted; the OQ-1 = conflict variant (TM-C4a)
and the switch left in v0.3.1, where K-1 is permanent.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tests.proof import harness, tolerances
from trestle.common.types import RequestOutcome, RunView

GAP = "G-C3"
KEY = "g-c3-key"
ARGS = {"message": "g-c3"}


def retry_after_edit(tmp_path: Path) -> tuple[RunView, RunView | RequestOutcome]:
    """Run `echo` under KEY, edit the plugin source (no semantic change),
    let the registry pick the edit up, and retry with the same key and
    args."""
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    source = plugin_dir / "echo.py"
    shutil.copyfile(harness.DEFAULT_PLUGIN_DIR / "echo.py", source)
    kernel = harness.fresh_kernel([plugin_dir], home=tmp_path / "home")

    first = kernel.control.run(
        plugin="echo", args=ARGS, wait_ms=tolerances.HARNESS_WAIT_MS, idempotency_key=KEY
    )
    assert isinstance(first, RunView) and first.state == "succeeded"
    before = kernel.registry.get("echo")
    assert before is not None

    source.write_text(source.read_text(encoding="utf-8") + "\n# edited\n", encoding="utf-8")
    kernel.registry.refresh()
    after = kernel.registry.get("echo")
    assert after is not None and after.snapshot_id != before.snapshot_id

    retry = kernel.control.run(
        plugin="echo", args=ARGS, wait_ms=tolerances.HARNESS_WAIT_MS, idempotency_key=KEY
    )
    return first, retry


@pytest.mark.proves("WR-IDEM-1", "A3.1", "A", "core", "PROC", "CI")
@pytest.mark.proves("WR-IDEM-1", "WR-IDEM-1:join-after-republish", "core", "core", "PROC", "CI")
def test_targetretry_after_edit_joins_same_run(tmp_path: Path) -> None:
    first, retry = retry_after_edit(tmp_path)
    assert isinstance(retry, RunView) and retry.run_id == first.run_id, (
        f"retry after a source edit was not joined to the first run: {retry!r}"
    )
