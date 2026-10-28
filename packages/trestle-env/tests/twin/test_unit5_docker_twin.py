"""L.RB-12.1 twin: WR-UNIT-5 on the fake binding (STUB · CI).

The same `tree_variants` plugin, parametrization and one call as `host/test_unit5_docker.py`, on
`twin.fake_binding`'s engine. Registers only `@stub-twin` labels and no matrix clause.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from twin import fake_binding, harness, variants

CASES = [
    pytest.param(
        "passed",
        marks=pytest.mark.stub_proven("WR-UNIT-5:b-passed-released@stub-twin"),
        id="passed",
    ),
    pytest.param(
        "sibling_failure",
        marks=pytest.mark.stub_proven("WR-UNIT-5:b-sibling-failure@stub-twin"),
        id="sibling_failure",
    ),
    pytest.param(
        "child_exception",
        marks=pytest.mark.stub_proven("WR-UNIT-5:b-child-exception@stub-twin"),
        id="child_exception",
    ),
]


@pytest.mark.parametrize("mode", CASES)
def test_child_container_released_by_root(mode: str, tmp_path: Path) -> None:
    state = tmp_path / "engine.json"
    with variants.variants_host(tmp_path / "home", harness.twin_environ(state)) as host:
        answer = variants.run_terminal(host, env=f"unit5-twin-{mode}", mode=mode)
        run_id = answer["run_id"]
        entries = harness.lane(harness.run_dir(host, run_id))
    assert (answer["answer"]["outcome"] == "passed") == (mode == "passed"), answer
    made = variants.created(entries)
    assert set(made) == {variants.POSTGRES, variants.HELPER}, made
    assert variants.claims_precede_creates(entries)
    assert variants.release_order(entries) == [variants.HELPER, variants.POSTGRES]
    assert variants.released_after_root_end(entries)
    assert harness.container_released(answer)
    after = fake_binding.read_state(state)
    prefix = harness.selector_prefix(run_id)
    assert not [c for c in after["containers"] if c["name"].startswith(prefix)]
