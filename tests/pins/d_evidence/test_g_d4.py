"""G-D4 (BFD-32): every dropped event appends one limit-marker line, so the
marker volume grows with the number of drops."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests.proof.markers import TargetUnmet, target_check
from trestle.child.context import RuntimeContext
from trestle.common.limits import CaptureLimits

KEPT_EVENTS = 5
FEW_DROPS = 200
MANY_DROPS = 2000


def _marker_lines_after_drops(root: Path, drops: int) -> int:
    """Emit `KEPT_EVENTS + drops` events against a cap of `KEPT_EVENTS`, and
    count the limit-marker lines the child recorded."""
    work = root / "work"
    evidence = root / "evidence"
    evidence.mkdir(parents=True)
    limits = CaptureLimits(max_event_count=KEPT_EVENTS, max_events_per_second=10**9)
    ctx = RuntimeContext(
        work=work,
        evidence=evidence,
        deadline=datetime.now(UTC),
        events_path=evidence / "events.ndjson",
        limits=limits,
    )
    for i in range(KEPT_EVENTS + drops):
        ctx.log(f"event-{i}")
    return len(ctx.limits_markers())


@pytest.mark.pin("G-D4")
def test_pin_marker_lines_track_drops(tmp_path: Path) -> None:
    few = _marker_lines_after_drops(tmp_path / "few", FEW_DROPS)
    many = _marker_lines_after_drops(tmp_path / "many", MANY_DROPS)
    assert many > few


@pytest.mark.target("G-D4")
@pytest.mark.proves("WR-EVID-4", "WR-EVID-4:markers-bounded", "core", "core", "must", "CI")
@pytest.mark.xfail(strict=True, raises=TargetUnmet, reason="defect:G-D4")
def test_target_marker_lines_equal_for_200_and_2000_drops(tmp_path: Path) -> None:
    few = _marker_lines_after_drops(tmp_path / "few", FEW_DROPS)
    many = _marker_lines_after_drops(tmp_path / "many", MANY_DROPS)
    target_check(
        many == few,
        "G-D4",
        f"marker lines: {few} after {FEW_DROPS} drops, {many} after {MANY_DROPS} drops",
    )
