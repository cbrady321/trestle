"""L.RB-11.1 twin: K-15 through `run` on the fake docker CLI (STUB · CI).

The same published pipeline, the same parametrization and the same one call per variant as
`host/test_pipeline_k15.py`; the stack runs on the fake docker CLI
(`trestle_pack_docker.fake_cli`) instead of the operator's docker. Registers only `@stub-twin`
labels.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from twin import k15


@pytest.mark.typed_records
@pytest.mark.stub_proven("WR-ENV-11:pipeline-one-teardown-host@stub-twin")
@pytest.mark.stub_proven("WR-ENV-11:entry-points-callable-host@stub-twin")
@pytest.mark.parametrize("variant", k15.VARIANTS)
def test_pipeline_stops_stack_once(variant: str, tmp_path: Path) -> None:
    state, environ = k15.twin_docker(tmp_path, fail_up=variant == "up_failure")
    project = k15.project_name(variant)
    work = k15.workdir(tmp_path, variant)
    with k15.pipeline_host(tmp_path / "home", environ=environ) as host:
        answer = k15.run_pipeline(host, work, project)
        k15.expect_state(variant, answer)
        assert k15.teardown_events(host, answer["run_id"]) == 1
    calls = k15.fake_calls(state)
    assert [c for c in calls if c.split()[0] == "down"] == ["down"]  # never with --volumes
    assert not [c for c in calls if c.split()[0] == "stop"]
    assert calls[-1] == "down"  # nothing of the stack is touched after it
