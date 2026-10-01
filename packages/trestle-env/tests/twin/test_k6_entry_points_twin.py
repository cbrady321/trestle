"""CI twin of `host/test_k6_entry_points.py` (L.RB-3.3; MC-B-03; STUB · CI): the same cases through
`run`, the legacy plugins on the in-memory engine backend (`twin.k6.EngineBackend`, the
`FakeComposeBackend` shape) and the reference plugin on the fake binding; the seeded volume is
present after each."""

from __future__ import annotations

from pathlib import Path

import pytest

from twin import fake_binding, harness, k6


@pytest.mark.stub_proven("WR-OWN-4:registered-entry-points-host@stub-twin")
@pytest.mark.parametrize("case", k6.CASES)
def test_no_volume_deleted_by_any_registered_teardown(case: str, tmp_path: Path) -> None:
    plugin, teardown = k6.split(case)
    if plugin == "reference_env":
        state = tmp_path / "engine.json"
        seeded = f"trestle-k6-ref-{tmp_path.name}"[:60]
        state.write_text(f'{{"volumes": ["{seeded}"]}}', encoding="utf-8")
        with harness.reference_host(tmp_path / "home", harness.twin_environ(state)) as host:
            answer = harness.run_terminal(host, env="k6-release-twin")
        assert answer["answer"]["outcome"] == "passed", answer
        after = fake_binding.read_state(state)
        assert seeded in after["volumes"]  # the release removed no volume
        assert [c for c in after["calls"] if c["member"] == "stop"]  # it did release
        return
    state = tmp_path / "engine.json"
    project = k6.project_name(case)
    work = k6.workdir(tmp_path, case)
    environ = {k6.FAKE_STATE_ENV: str(state)}
    with k6.legacy_host(tmp_path / "home", plugin, twin=True, environ=environ) as host:
        answer = k6.call(host, plugin, k6.legacy_args(plugin, teardown, project, work))
    k6.expect_state(plugin, answer)
    engine = k6.engine_state(state)
    assert engine["volumes"] == {k6.volume_name(project): "seeded"}  # present after the teardown
    ups = ["up", "up"] if plugin == "docker_stack" else ["up"]
    assert engine["calls"] == ups + k6.teardown_calls(plugin, teardown)
