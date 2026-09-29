"""G-E1: an unknown service named in an explicit wave is silently dropped
(BFD-44). The filter in `_plan_explicit_waves` removes the name before the
`unknown service in wave` check, so that check is dead."""

from __future__ import annotations

import pytest

from tests.proof.markers import target_check
from trestle_packs.core.dag import plan_waves

SERVICES = ["db", "web"]
WAVES = [["db", "ghost"], ["web"]]


@pytest.mark.pin("G-E1")
def test_pin_unknown_wave_name_dropped() -> None:
    plan = plan_waves(SERVICES, explicit_waves=WAVES)
    assert plan.waves == (("db",), ("web",))
    assert "ghost" not in plan.flat()


@pytest.mark.target("G-E1")
@pytest.mark.proves(
    "WR-ENV-1",
    "WR-ENV-1:legacy-explicit-waves-refuse-unknown-duplicate",
    "core",
    "core",
    "must",
    "CI",
)
@pytest.mark.xfail(strict=True, reason="defect:G-E1")
def test_target_unknown_wave_name_refused() -> None:
    refused = False
    try:
        plan_waves(SERVICES, explicit_waves=WAVES)
    except ValueError:
        refused = True
    target_check(refused, "G-E1", "unknown service 'ghost' in an explicit wave was not refused")
