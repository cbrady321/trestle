"""L.RB-12.6 twin: the WR-OWN-3 container half on the fake binding (STUB · CI).

The same falsifier as `host/test_own3_container_half.py`: a passed reference run and a passed
`tree_variants` run, each on its own fake engine; after each answer the engine holds no container
named with the run's selector prefix, and neither does any other passed run this session made
through the harness on these engines. Registers only the `@stub-twin` label.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from twin import fake_binding, harness, variants


@pytest.mark.stub_proven("WR-OWN-3:created-container-stopped-on-success@stub-twin")
def test_passed_run_leaves_no_created_container(tmp_path: Path) -> None:
    state = tmp_path / "engine.json"
    environ = harness.twin_environ(state)
    with harness.reference_host(tmp_path / "ref", environ) as host:
        reference = harness.run_terminal(host, env="own3-twin-ref")
    with variants.variants_host(tmp_path / "var", environ) as host:
        tree = variants.run_terminal(host, env="own3-twin-var", mode="passed")
    ours = [str(reference["run_id"]), str(tree["run_id"])]
    for answer in (reference, tree):
        assert answer["answer"]["outcome"] == "passed", answer
    assert set(ours) <= set(harness.PASSED_RUNS)
    names = [c["name"] for c in fake_binding.read_state(state)["containers"]]
    created = [
        c["selector"] for c in fake_binding.read_state(state)["calls"] if c["member"] == "create"
    ]
    assert created, "the runs created containers: the check is not vacuous"
    for run_id in harness.PASSED_RUNS:
        assert not [n for n in names if n.startswith(harness.selector_prefix(run_id))], run_id
