"""L.RB-12.5 twin: an injected container-release failure beside a passed primary, on the fake
binding (STUB · CI). The same `tree_variants` run (`passed`) as
`host/test_cleanup_beside_primary_docker.py`: the fake engine's own stop leaves each container
(stopped) and answers unknown, and the allowlisted release wrapper over `twin/state_docker.py`
fails `remove_argv`, so the sweep cannot release them either. Registers only the `@stub-twin`
label.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from twin import fake_binding, harness, release, variants


@pytest.mark.stub_proven("WR-OWN-6:docker-inventory@stub-twin")
def test_cleanup_failure_beside_primary_matches_docker_inventory(tmp_path: Path) -> None:
    state = tmp_path / "engine.json"
    script, _log = release.wrapper(tmp_path / "bin", release.fake_real(state), "remove")
    home = tmp_path / "home"
    release.operator_config(home, [str(script)])
    environ = {
        **harness.twin_environ(state),
        fake_binding.DOCKER_ENV: str(script),
        fake_binding.FAIL_ENV: "remove",
    }
    with variants.variants_host(home, environ) as host:
        answer = variants.run_terminal(host, env="own6-twin", mode="passed", note=False)
        run_dir = harness.run_dir(host, answer["run_id"])
    assert answer["answer"]["outcome"] == "passed", answer  # the primary is unchanged
    cleanup = answer["answer"]["cleanup"]
    assert cleanup["unknown"] >= 1 and cleanup["clean"] is False, cleanup
    assert variants.cleanup_beside_primary(run_dir)
    names = [c["name"] for c in fake_binding.read_state(state)["containers"]]
    prefix = harness.selector_prefix(answer["run_id"])
    assert sorted(n for n in names if n.startswith(prefix)) == [
        prefix + variants.HELPER,
        prefix + variants.POSTGRES,
    ]  # the inventory shows what the answer calls unknown: present, never reported clean
