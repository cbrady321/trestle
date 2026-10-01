"""L.RB-12.2 twin: WR-UNIT-6 on the fake binding (STUB · CI).

The same `tree_variants` plugin, parametrization and calls as `host/test_unit6_docker.py`, on
`twin.fake_binding`'s engine with a found container planted in its state first. Registers only
`@stub-twin` labels and no matrix clause.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from tests.core.spine import support
from tests.proof import tolerances

from twin import fake_binding, harness, variants

FOUND = {"name": "trestle-found-unit6", "state": "running", "port": 0, "environment": {}}

CASES = [
    pytest.param(
        "cancel",
        marks=[
            pytest.mark.stub_proven("WR-UNIT-5:b-root-cancel@stub-twin"),
            pytest.mark.stub_proven("WR-UNIT-6:b-no-action-after-stop@stub-twin"),
        ],
        id="cancel",
    ),
    pytest.param(
        "deadline",
        marks=[
            pytest.mark.stub_proven("WR-UNIT-5:b-root-deadline@stub-twin"),
            pytest.mark.stub_proven("WR-UNIT-6:b-no-action-after-stop@stub-twin"),
        ],
        id="deadline",
    ),
]


@pytest.mark.parametrize("mode", CASES)
def test_after_root_stop_no_child_container_remains(mode: str, tmp_path: Path) -> None:
    state = tmp_path / "engine.json"
    state.write_text(json.dumps({"containers": [FOUND]}), encoding="utf-8")
    with variants.variants_host(tmp_path / "home", harness.twin_environ(state)) as host:
        if mode == "cancel":
            run_id = variants.start(host, env="unit6-twin-cancel", mode=mode)
            assert support.wait_until(
                lambda: variants.both_created(host, run_id), tolerances.JOIN_WAIT_S * 3
            ), "both containers were never created"
            view = variants.cancel_and_await(host, run_id)
        else:
            view = variants.run_terminal(host, env="unit6-twin-deadline", mode=mode)
            run_id = view["run_id"]
        run_dir = harness.run_dir(host, run_id)
    expected = {"cancel": "cancelled", "deadline": "timed_out"}[mode]
    assert view["answer"]["outcome"] == expected, view
    assert variants.applied_past_stop(run_dir) == []
    after = fake_binding.read_state(state)
    prefix = harness.selector_prefix(run_id)
    assert not [c for c in after["containers"] if c["name"].startswith(prefix)]
    assert [c for c in after["containers"] if c["name"] == FOUND["name"]] == [FOUND]  # untouched
    touched = [c for c in after["calls"] if c.get("selector") == FOUND["name"]]
    assert touched == [], touched
