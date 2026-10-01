"""G-E1: an unknown service named in an explicit wave is refused (BFD-44), flipped by
L.NW-1.3. It used to be silently dropped: the filter in `_plan_explicit_waves` removed the name
before the `unknown service in wave` check, so that check was dead."""

from __future__ import annotations

import pytest
from trestle_packs.core.dag import plan_waves

from tests.proof.markers import target_check

SERVICES = ["db", "web"]
WAVES = [["db", "ghost"], ["web"]]


@pytest.mark.proves(
    "WR-ENV-1",
    "WR-ENV-1:legacy-explicit-waves-refuse-unknown-duplicate",
    "core",
    "core",
    "must",
    "CI",
)
def test_target_unknown_wave_name_refused() -> None:
    refused = False
    try:
        plan_waves(SERVICES, explicit_waves=WAVES)
    except ValueError:
        refused = True
    target_check(refused, "G-E1", "unknown service 'ghost' in an explicit wave was not refused")
