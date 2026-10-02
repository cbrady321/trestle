"""CI twin of `host/test_healthy_machine_full.py` (L.RB-8.3; MC-B-03; STUB · CI, the owned app a
real local process): the same test plugin, the same kill, the same facts, with the containers on
the fake binding's engine, in which the found `postgres` and its volume are planted before the
run."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trestle_env import tree
from trestle_env.plugins import _bind
from twin import fake_binding, harness, healthy_case

FOUND = tree.POSTGRES_SERVICE
VOLUME = "reuse-postgres_reuse_data"


@pytest.mark.stub_proven("WR-ENV-10:dispositions-complete@stub-twin")
def test_one_call_reused_started_repaired_found_untouched_no_volume_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "engine.json"
    # no password in its environment: every exec check (identity, configuration, readiness) holds
    planted = {"name": FOUND, "state": "running", "port": _bind.POSTGRES_PORT, "environment": {}}
    state.write_text(json.dumps({"containers": [planted], "volumes": [VOLUME]}), encoding="utf-8")
    environ, log = healthy_case.app_environ(tmp_path)
    for name, value in {**harness.twin_environ(state), **environ}.items():
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    monkeypatch.setenv("PYTHONPATH", harness.plugin_pythonpath())
    kernel = healthy_case.kernel(tmp_path)
    done, pid, port = healthy_case.run_and_kill(kernel, "healthy-twin", log)
    healthy_case.assert_repaired(kernel, done, pid, log, port, FOUND)
    after = fake_binding.read_state(state)
    prefix = harness.selector_prefix(done.run_id)
    assert not [c for c in after["containers"] if c["name"].startswith(prefix)]  # created: gone
    (found,) = [c for c in after["containers"] if c["name"] == FOUND]
    assert found["state"] == "running"  # the found one: untouched
    touched = [c for c in after["calls"] if c.get("selector") == FOUND and c["member"] != "check"]
    assert touched == [], touched
    assert after["volumes"] == [VOLUME]  # no volume removed
