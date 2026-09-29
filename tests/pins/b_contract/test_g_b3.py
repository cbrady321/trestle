"""G-B3 (BFD-04, BFD-15): a plugin exception leaves no explanation in the
run view. The child's `except Exception` returns 1 and nothing writes the
message, so no run file holds it and `RunView.error` stays empty."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.pins.b_contract.kit import run_plugin
from tests.pins.b_contract.plugins.raises import SENTINEL
from tests.proof.markers import target_check


def _run(tmp_path: Path):
    view, run_dir = run_plugin(tmp_path, "raises")
    assert view.state == "failed"
    return view, run_dir


@pytest.mark.pin("G-B3")
def test_pin_no_run_file_contains_message(tmp_path: Path) -> None:
    view, run_dir = _run(tmp_path)
    files = [p for p in run_dir.rglob("*") if p.is_file()]
    assert files
    holders = [p for p in files if SENTINEL.encode() in p.read_bytes()]
    assert holders == []
    assert view.error is None


@pytest.mark.target("G-B3")
@pytest.mark.proves("A9.1", "A9.1", "A", "core", "must", "CI")
@pytest.mark.proves(
    "WR-EVID-1", "WR-EVID-1:sentinel-in-runview-error", "core", "core", "must", "CI"
)
@pytest.mark.xfail(strict=True, reason="defect:G-B3")
def test_target_runview_error_carries_message(tmp_path: Path) -> None:
    view, _run_dir = _run(tmp_path)
    target_check(
        view.error is not None and SENTINEL in str(view.error),
        "G-B3",
        f"RunView.error is {view.error!r}, not carrying the plugin's message",
    )
