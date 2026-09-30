"""G-B3 (BFD-04, BFD-15), flipped by L.CS-3.3: a plugin exception is explained in the run view.

The child writes `evidence/child_error.json` (L.CS-3.1), the conductor folds it into the ledger's
`error_record` row (L.CS-3.2), and `RunView.error` reads that row."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.pins.b_contract.kit import run_plugin
from tests.pins.b_contract.plugins.raises import SENTINEL
from tests.proof.markers import target_check


@pytest.mark.proves("A9.1", "A9.1", "A", "core", "must", "CI")
@pytest.mark.proves(
    "WR-EVID-1", "WR-EVID-1:sentinel-in-runview-error", "core", "core", "must", "CI"
)
def test_target_runview_error_carries_message(tmp_path: Path) -> None:
    view, _run_dir = run_plugin(tmp_path, "raises")
    assert view.state == "failed"
    target_check(
        view.error is not None and SENTINEL in str(view.error),
        "G-B3",
        f"RunView.error is {view.error!r}, not carrying the plugin's message",
    )
