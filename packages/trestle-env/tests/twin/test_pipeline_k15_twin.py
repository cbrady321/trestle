"""L.RB-11.1 twin: K-15 through `run` on the recording in-memory compose backend (STUB · CI).

The same published pipeline, the same parametrization and the same one call per variant as
`host/test_pipeline_k15.py`; the stack runs on `twin.k15.RecordingComposeBackend` (the
`FakeComposeBackend` shape) instead of the operator's docker. Registers only `@stub-twin` labels.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from twin import k15


@pytest.mark.stub_proven("WR-ENV-11:pipeline-one-teardown-host@stub-twin")
@pytest.mark.stub_proven("WR-ENV-11:entry-points-callable-host@stub-twin")
@pytest.mark.parametrize("variant", k15.VARIANTS)
def test_pipeline_stops_stack_once(variant: str, tmp_path: Path) -> None:
    state = tmp_path / "compose-calls.jsonl"
    environ = {
        k15.FAKE_STATE_ENV: str(state),
        k15.FAKE_FAIL_UP_ENV: "1" if variant == "up_failure" else None,
    }
    project = k15.project_name(variant)
    work = k15.workdir(tmp_path, variant)
    with k15.pipeline_host(tmp_path / "home", twin=True, environ=environ) as host:
        answer = k15.run_pipeline(host, work, project)
        k15.expect_state(variant, answer)
        assert k15.teardown_events(host, answer["run_id"]) == 1
    calls = k15.fake_calls(state)
    downs = [c for c in calls if c["call"] == "down"]
    assert downs == [{"call": "down", "project": project, "remove_volumes": False}]
    assert not [c for c in calls if c["call"] == "stop"]
    assert [c["call"] for c in calls][-1] == "down"  # nothing of the stack is touched after it
