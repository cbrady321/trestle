"""G-A4 (BFD-06): a status request waits behind a slow publication probe
inside `run`.

A dropped-in plugin with a module-level sleep is validated on the event
loop inside the `run` tool (`maybe_refresh`, then the synchronous admit),
so a concurrent status request is not answered until the probe is over.
Order only (WR-PROOF-9): the probe delay is a proof-court patience value,
the assertion is which finished first.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

import pytest

from tests.pins.a_lifecycle import helpers
from tests.proof import mcp_host, tolerances
from tests.proof.markers import target_check

PROBE_DELAY_S = tolerances.SETTLE_LONG_S * 2


def _events(log: Path) -> list[tuple[str, float]]:
    if not log.exists():
        return []
    out: list[tuple[str, float]] = []
    for line in log.read_text(encoding="utf-8").splitlines():
        tag, _, stamp = line.partition(" ")
        out.append((tag, float(stamp)))
    return out


def _status_during_probe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[float, float]:
    """Hold a `run` of a slow-import plugin, issue a status request while the
    probe is in flight, and return (status answered at, first probe end)."""
    log = tmp_path / "probe.log"
    monkeypatch.setenv("TRESTLE_PROBE_LOG", str(log))
    monkeypatch.setenv("TRESTLE_PROBE_DELAY_S", str(PROBE_DELAY_S))
    with mcp_host.McpHost() as host:
        shutil.copy(helpers.LANE_PLUGIN_DIR / "slow_import.py", host.home / "plugins")
        held = host.hold("run", {"plugin": "slow_import", "wait_ms": 0})
        assert helpers.wait_until(
            lambda: any(tag == "start" for tag, _ in _events(log)), tolerances.JOIN_WAIT_S
        ), "probe never started"
        status = host.hold("await_runs", {"run_ids": ["r_unknown"], "timeout_ms": 0})
        host.join(status, timeout=tolerances.JOIN_WAIT_S + PROBE_DELAY_S * 4)
        answered = time.time()
        host.join(held, timeout=tolerances.JOIN_WAIT_S + PROBE_DELAY_S * 4)
    ends = [stamp for tag, stamp in _events(log) if tag == "end"]
    assert ends, "probe never ended"
    return answered, min(ends)


@pytest.mark.pin("G-A4")
def test_pin_query_answered_after_probe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    answered, first_end = _status_during_probe(tmp_path, monkeypatch)
    assert answered >= first_end


@pytest.mark.target("G-A4")
@pytest.mark.proves(
    "WR-TERM-7", "WR-TERM-7:status-during-slow-admission", "core", "core", "MCP+PROC", "CI"
)
@pytest.mark.xfail(strict=True, reason="defect:G-A4")
def test_target_query_answered_before_probe_ends(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    answered, first_end = _status_during_probe(tmp_path, monkeypatch)
    target_check(
        answered < first_end,
        "G-A4",
        f"status request answered {answered - first_end:.2f}s after the probe ended",
    )
