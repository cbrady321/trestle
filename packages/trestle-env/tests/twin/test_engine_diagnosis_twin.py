"""CI twin of `host/test_engine_diagnosis.py` (L.RB-7.1; MC-B-03; STUB · CI): the same plugin and
the same one call, with the fake binding's engine not answering (`unreachable_ports`). The host
engine the node checks around the run is the fake engine's own state: the run reached nothing
there (no create, no stop, no container)."""

from __future__ import annotations

from pathlib import Path

import pytest

from twin import engine_diagnosis, fake_binding, harness


@pytest.mark.stub_proven("WR-ENV-6:host-engine-unchanged@stub-twin")
def test_unreachable_endpoint_diagnosed_distinct_from_cli_missing(tmp_path: Path) -> None:
    state = tmp_path / "engine.json"
    before = fake_binding.read_state(state)
    environ = harness.twin_environ(state, fake_binding.UNREACHABLE_SEAM)
    with harness.reference_host(tmp_path / "home", environ) as host:
        answer = harness.run_terminal(host, env="engine-unreachable-twin")
    engine_diagnosis.assert_diagnosed_unreachable(answer)
    after = fake_binding.read_state(state)
    assert after["containers"] == before["containers"] == []
    assert {c["member"] for c in after["calls"]} <= {"observe", "check"}  # nothing was changed
