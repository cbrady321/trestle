"""CI twin of `host/test_cancel_readiness.py` (L.RB-10.1; MC-B-03; STUB · CI, the owned app a real
local process): the same test plugin, the same cancel, the same facts, with the containers on the
fake binding's engine, in which the found `postgres` is planted before the run."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trestle_env import tree
from trestle_env.plugins import _bind
from twin import cancel_case, fake_binding, harness, overrides

FOUND = tree.POSTGRES_SERVICE


@pytest.mark.stub_proven("WR-CANCEL-4:b-created-gone-found-untouched@stub-twin")
@pytest.mark.stub_proven("WR-OWN-2:b-cancel-path@stub-twin")
def test_cancel_during_readiness_owned_and_created_gone_found_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "engine.json"
    planted = {"name": FOUND, "state": "running", "port": _bind.POSTGRES_PORT}
    planted["environment"] = {"POSTGRES_PASSWORD": tree.POSTGRES_FIXTURE_PASSWORD}
    state.write_text(json.dumps({"containers": [planted]}), encoding="utf-8")
    environ, log = cancel_case.app_environ(tmp_path)
    for name, value in {**harness.twin_environ(state), **environ}.items():
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    monkeypatch.setenv("PYTHONPATH", harness.plugin_pythonpath())
    kernel = cancel_case.kernel(tmp_path)
    done, elapsed, port = cancel_case.cancel_mid_readiness(kernel, "cancel-twin", log)
    cancel_case.assert_cancel_facts(kernel, done, elapsed, log, port, FOUND)
    after = fake_binding.read_state(state)
    prefix = harness.selector_prefix(done.run_id)
    assert not [c for c in after["containers"] if c["name"].startswith(prefix)]  # created: gone
    (found,) = [c for c in after["containers"] if c["name"] == FOUND]
    assert found["state"] == "running"  # the found one: untouched
    touched = [c for c in after["calls"] if c.get("selector") == FOUND and c["member"] != "check"]
    assert touched == [], touched


@pytest.mark.stub_proven("WR-CANCEL-4:b-created-gone-found-untouched@stub-twin")
@pytest.mark.stub_proven("WR-OWN-2:b-cancel-path@stub-twin")
def test_reference_tree_cancel_during_readiness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = tmp_path / "engine.json"
    environ = {
        **harness.twin_environ(state, fake_binding.WRONG_PASSWORD_SEAM),
        **overrides.operator_environ(tmp_path / "operator"),
    }
    done, _ = cancel_case.reference_cancel(tmp_path, monkeypatch, environ, "cancel-ref-twin")
    after = fake_binding.read_state(state)
    prefix = harness.selector_prefix(done.run_id)
    assert not [c for c in after["containers"] if c["name"].startswith(prefix)]  # released
