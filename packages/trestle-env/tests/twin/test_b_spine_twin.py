"""L.RB-0.4 twins: the B-spine facts on the fake binding (MC-B-03; STUB · CI).

The same published `reference_env` plugin, the same tree and the same one MCP call as the HOST
nodes in `host/test_b_spine.py`, with the port map swapped for `twin.fake_binding` (AMB-2). A twin
registers only the `@stub-twin` labels and no matrix clause; the passed-call twin joins `-m spine`
(the wrong-password twin waits out the declared readiness wait, so it stays out of the spine gate).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from trestle_env import tree
from twin import fake_binding, harness

POSTGRES = tree.POSTGRES_UNIT
SUPPORT = tree.HTTP_SUPPORT_UNIT


@pytest.mark.spine  # the B-spine twin joins the spine gate (MC-29)
@pytest.mark.stub_proven("WR-ENV-10:one-request-dispositions-match-snapshot@stub-twin")
def test_one_call_passed_healthy_machine(tmp_path: Path) -> None:
    state = tmp_path / "engine.json"
    before = fake_binding.read_state(state)  # the engine snapshot before the call: nothing runs
    with harness.reference_host(tmp_path / "home", harness.twin_environ(state)) as host:
        answer = harness.run_terminal(host, env="b-spine-twin")
        assert answer["state"] == "succeeded", answer
        assert answer["outcome"]["class"] == "passed", answer
        assert answer["answer"]["outcome"] == "passed", answer
        run_id = answer["run_id"]
        entries = harness.lane(harness.run_dir(host, run_id))
    # dispositions equal the engine snapshot: neither service was there before, so both started
    assert not before["containers"]
    assert harness.dispositions(answer) == {SUPPORT: "started", POSTGRES: "started"}
    assert harness.claim_precedes_create(entries)
    assert harness.container_released(answer)
    after = fake_binding.read_state(state)
    prefix = harness.selector_prefix(run_id)
    created = [c["selector"] for c in after["calls"] if c["member"] == "create"]
    assert created and all(s.startswith(prefix) for s in created)
    assert not [c for c in after["containers"] if c["name"].startswith(prefix)]  # absent after
    stops = [c["selector"] for c in after["calls"] if c["member"] == "stop"]
    assert sorted(stops) == sorted(created)  # each released once, through the owned stop


@pytest.mark.stub_proven("WR-ENV-10:readiness-authenticated-postgres@stub-twin")
def test_wrong_postgres_password_never_ready(tmp_path: Path) -> None:
    state = tmp_path / "engine.json"
    environ = harness.twin_environ(state, fake_binding.WRONG_PASSWORD_SEAM)
    with harness.reference_host(tmp_path / "home", environ) as host:
        answer = harness.run_terminal(host, env="b-spine-twin-wrong")
        run_id = answer["run_id"]
    assert answer["state"] == "succeeded", answer  # the run finished; its class is the verdict
    assert answer["answer"]["outcome"] != "passed", answer  # the tree's class, not the plugin's
    assert harness.dispositions(answer).get(POSTGRES) != "started"
    after = fake_binding.read_state(state)
    checks = [c for c in after["calls"] if c["member"] == "check"]
    postgres = [c for c in checks if c["check"] == tree.POSTGRES_READY]
    assert postgres and not any(c["satisfied"] for c in postgres), "readiness never passed"
    # the only other check is the supporting service's, which passed before Postgres was made
    assert {c["check"] for c in checks} == {tree.HTTP_SUPPORT_READY, tree.POSTGRES_READY}
    prefix = harness.selector_prefix(run_id)
    assert not [c for c in after["containers"] if c["name"].startswith(prefix)]
