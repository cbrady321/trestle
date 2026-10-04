"""L.RB-8.2: a local override on the operator's real Docker (DOCKER+PROC · HOST; run only by the
host-docker gate).

The `local_override` test plugin (`fixtures/local_override.py`): the supporting service is a CHOICE
between its Docker node and the catalog's agent-launched override, selected by the request. One
`run` through the MC-12 MCP host, bound to the operator's docker at the gate's endpoint, with the
override running as a real local process launched by the toolchain-resolved interpreter. Facts are
read from outside: the engine inventory (the gate's own `inventory.snapshot`), the run's lane and
the evidence the launch recorded. Each node's CI twin is `twin/test_local_override_twin.py` (same
node name, the fake binding).

* the override runs: no container carrying the run's selector prefix exists at any point after the
  call, and the override app's recorded `argv[0]` is the absolute interpreter the toolchain
  resolved (never a `PATH` lookup);
* the operator's repository absent: the node is BLOCKED `environment.repository_missing` and no
  Docker container is started as a fallback.

The `test_reference_tree_*` nodes prove the same on the SHIPPED reference tree (`reference_env`,
V03 stage 10): `overrides=[http_support_local]` selects the local alternative of the
`http_support` CHOICE, Postgres stays in Docker (V-7.2 step 1 pins its fallback), and the
override's command is bound for the run by the plugin's own local router.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest
from tests.proof.host.docker_gate import inventory
from twin import harness, overrides

pytestmark = pytest.mark.docker_host


def _containers() -> list[str]:
    cli = shutil.which("docker")  # the operator's docker; the adapter itself never searches
    assert cli is not None, "the host-docker gate runs with a docker CLI (preflight)"
    snap = inventory.snapshot(cli, os.environ.get("TRESTLE_DOCKER_ENDPOINT"))
    assert snap["engine"]["reachable"], snap["engine"]
    return [name for obj in snap["containers"] for name in inventory.object_names(obj)]


@pytest.mark.proves(
    "WR-ENV-2", "WR-ENV-2:override-container-never-exists", "B", "B", "DOCKER+PROC", "HOST"
)
def test_override_docker_container_never_exists(tmp_path: Path) -> None:
    before = _containers()
    environ = {**harness.host_environ(), **overrides.operator_environ(tmp_path / "operator")}
    with overrides.overrides_host(tmp_path / "home", environ) as host:
        answer = overrides.run_terminal(
            host, "override-host", [overrides.LOGICAL], [overrides.OVERRIDE]
        )
        assert answer["state"] == "succeeded", answer
        assert answer["answer"]["outcome"] == "passed", answer
        run_id = answer["run_id"]
        run_dir = harness.run_dir(host, run_id)
        entries = harness.lane(run_dir)
        launches = overrides.launch_events(run_dir)
    prefix = harness.selector_prefix(run_id)
    created = [e for e in entries if e["class"] == "confirmation" and e["effect"] == "up"]
    assert [e["path"] for e in created] == [f"{overrides.LOGICAL}/{overrides.OVERRIDE}"]
    assert not [n for n in _containers() if n.startswith(prefix)]  # no container with the selector
    assert sorted(_containers()) == sorted(before)  # and the engine is as it was
    (launch,) = launches
    argv0 = overrides.unredacted(launch["payload"]["argv0"])
    assert argv0 == sys.executable and Path(argv0).is_absolute()


@pytest.mark.proves(
    "WR-ENV-2", "WR-ENV-2:missing-repo-no-docker-fallback", "B", "B", "DOCKER+PROC", "HOST"
)
def test_missing_repo_blocked_no_docker_start(tmp_path: Path) -> None:
    before = _containers()
    operator = overrides.operator_environ(tmp_path / "operator", repository=None)
    with overrides.overrides_host(
        tmp_path / "home", {**harness.host_environ(), **operator}
    ) as host:
        answer = overrides.run_terminal(
            host, "override-host-missing", [overrides.LOGICAL], [overrides.OVERRIDE]
        )
        run_id = answer["run_id"]
    assert answer["answer"]["outcome"] != "passed", answer
    node = answer["answer"]["primary"]  # the node the answer is about: the override
    assert node["path"] == [overrides.LOGICAL, overrides.OVERRIDE]
    assert (node["condition"], node["code"]) == ("blocked", "environment.repository_missing")
    assert not [n for n in _containers() if n.startswith(harness.selector_prefix(run_id))]
    assert sorted(_containers()) == sorted(before)  # no Docker container was started instead


@pytest.mark.proves(
    "WR-ENV-2", "WR-ENV-2:override-container-never-exists", "B", "B", "DOCKER+PROC", "HOST"
)
def test_reference_tree_local_override(tmp_path: Path) -> None:
    before = _containers()
    environ = {**harness.host_environ(), **overrides.operator_environ(tmp_path / "operator")}
    with harness.reference_host(tmp_path / "home", environ) as host:
        answer = harness.run_terminal(host, "override-ref", overrides=[overrides.OVERRIDE])
        assert answer["state"] == "succeeded", answer
        assert answer["answer"]["outcome"] == "passed", answer
        run_id = answer["run_id"]
        run_dir = harness.run_dir(host, run_id)
        entries = harness.lane(run_dir)
        launches = overrides.launch_events(run_dir)
    created = [e for e in entries if e["class"] == "confirmation" and e["effect"] == "up"]
    local = f"{overrides.LOGICAL}/{overrides.OVERRIDE}"
    assert sorted(e["path"] for e in created) == [local, "postgres"]  # postgres is in Docker
    docker_selector = f"{harness.selector_prefix(run_id)}{overrides.LOGICAL}."
    assert not [e for e in entries if str(e.get("identity", "")).startswith(docker_selector)]
    assert harness.dispositions(answer) == {overrides.LOGICAL: "started", "postgres": "started"}
    assert not [n for n in _containers() if n.startswith(harness.selector_prefix(run_id))]
    assert sorted(_containers()) == sorted(before)  # the run's own container released
    (launch,) = launches
    argv0 = overrides.unredacted(launch["payload"]["argv0"])
    assert argv0 == sys.executable and Path(argv0).is_absolute()


@pytest.mark.proves(
    "WR-ENV-2", "WR-ENV-2:missing-repo-no-docker-fallback", "B", "B", "DOCKER+PROC", "HOST"
)
def test_reference_tree_override_unbound_blocks(tmp_path: Path) -> None:
    before = _containers()
    operator = overrides.operator_environ(tmp_path / "operator", repository=None)
    with harness.reference_host(tmp_path / "home", {**harness.host_environ(), **operator}) as host:
        answer = harness.run_terminal(host, "override-ref-missing", overrides=[overrides.OVERRIDE])
        run_id = answer["run_id"]
        entries = harness.lane(harness.run_dir(host, run_id))
    assert answer["answer"]["outcome"] == "blocked", answer
    node = answer["answer"]["primary"]
    assert node["path"] == [overrides.LOGICAL, overrides.OVERRIDE]
    assert (node["condition"], node["code"]) == ("blocked", "environment.repository_missing")
    assert node["human_action"]
    assert not [e for e in entries if e["class"] == "issue"]  # blocked before any effect
    assert not [n for n in _containers() if n.startswith(harness.selector_prefix(run_id))]
    assert sorted(_containers()) == sorted(before)  # no Docker container was started instead
