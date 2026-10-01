"""L.RB-8.2 twins: the local override on the fake binding (MC-B-03; STUB · CI).

The same `local_override` test plugin, the same one MCP call and the same facts as
`host/test_local_override.py`, with the container port swapped for `twin.fake_binding`'s engine. The
override itself is the REAL local process port running the real override app: a local override
never reaches the engine, so the twin's engine must end as it began, untouched. A twin registers
only `@stub-twin` labels and no matrix clause.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from twin import fake_binding, harness, overrides


@pytest.mark.stub_proven("WR-ENV-2:override-container-never-exists@stub-twin")
def test_override_docker_container_never_exists(tmp_path: Path) -> None:
    state = tmp_path / "engine.json"
    environ = {**harness.twin_environ(state), **overrides.operator_environ(tmp_path / "operator")}
    with overrides.overrides_host(tmp_path / "home", environ) as host:
        answer = overrides.run_terminal(
            host, "override-twin", [overrides.LOGICAL], [overrides.OVERRIDE]
        )
        assert answer["state"] == "succeeded", answer
        assert answer["answer"]["outcome"] == "passed", answer
        run_dir = harness.run_dir(host, answer["run_id"])
        entries = harness.lane(run_dir)
        launches = overrides.launch_events(run_dir)
    node = overrides.override_state(answer)
    assert (node["condition"], node["disposition"]) == ("satisfied", "started")
    created = [e for e in entries if e["class"] == "confirmation" and e["effect"] == "up"]
    assert [e["path"] for e in created] == [f"{overrides.LOGICAL}/{overrides.OVERRIDE}"]
    assert str(created[0]["identity"]).startswith("proc-")  # a local process, not `trwr-<run>-`
    assert harness.claim_precedes_create(entries)
    # the Docker node's container never exists: the engine was never asked for anything
    after = fake_binding.read_state(state)
    assert after == {"containers": [], "created": [], "volumes": [], "calls": []}
    assert not [
        c
        for c in after["containers"]
        if c["name"].startswith(harness.selector_prefix(answer["run_id"]))
    ]
    # the override app was launched by the toolchain-resolved absolute interpreter
    (launch,) = launches
    argv0 = overrides.unredacted(launch["payload"]["argv0"])
    assert argv0 == sys.executable and Path(argv0).is_absolute()
    assert launch["payload"]["version"].startswith("3.12")
    assert int(answer["answer"]["cleanup"]["released"]) >= 1  # the local process is released


@pytest.mark.stub_proven("WR-ENV-2:missing-repo-no-docker-fallback@stub-twin")
def test_missing_repo_blocked_no_docker_start(tmp_path: Path) -> None:
    state = tmp_path / "engine.json"
    operator = overrides.operator_environ(tmp_path / "operator", repository=None)
    environ = {**harness.twin_environ(state), **operator}
    with overrides.overrides_host(tmp_path / "home", environ) as host:
        answer = overrides.run_terminal(
            host, "override-twin-missing", [overrides.LOGICAL], [overrides.OVERRIDE]
        )
        entries = harness.lane(harness.run_dir(host, answer["run_id"]))
    assert answer["answer"]["outcome"] != "passed", answer
    node = answer["answer"]["primary"]  # the node the answer is about: the override
    assert node["path"] == [overrides.LOGICAL, overrides.OVERRIDE]
    assert (node["condition"], node["code"]) == ("blocked", "environment.repository_missing")
    assert node["human_action"] and "override-app" in node["human_action"]
    assert not [e for e in entries if e["class"] == "issue" and e["effect"] == "up"]  # nothing made
    # no fallback: the engine was never asked to create (or even read) the Docker node
    after = fake_binding.read_state(state)
    assert after == {"containers": [], "created": [], "volumes": [], "calls": []}
