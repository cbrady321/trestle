"""HOST: a `services` selection walks only its Compose closure (C-3; DOCKER · HOST, run only by the
host-docker gate).

The reference plugin is bound to the operator's docker with the pinned images and the Compose
definition `closure_case.COMPOSE` (where `postgres` `depends_on` `http_support`). Selecting
`http_support`, a closure of its own, starts that one service: the run passes, the only service
this run created is `http_support`, and the answer lists no `postgres` vertex (an unselected
service is not a plan vertex). Nothing of the run remains on the engine afterwards.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from tests.proof.host.docker_gate import inventory
from twin import closure_case, harness

from trestle_env import tree

pytestmark = pytest.mark.docker_host


def test_a_closed_selection_starts_only_its_services(tmp_path: Path) -> None:
    cli = shutil.which("docker")  # the operator's docker; the adapter itself never searches
    assert cli is not None, "the host-docker gate runs with a docker CLI (preflight)"
    endpoint = os.environ.get("TRESTLE_DOCKER_ENDPOINT")
    environ = closure_case.environ(harness.host_environ())
    with harness.reference_host(tmp_path / "home", environ) as host:
        answer = closure_case.call(host, "selection-host", [tree.HTTP_SUPPORT_SERVICE])
        assert answer["state"] == "succeeded", answer
        assert answer["answer"]["outcome"] == "passed", answer
        run_id = answer["run_id"]
        entries = harness.lane(harness.run_dir(host, run_id))
    assert closure_case.started(answer, entries, run_id) == {tree.HTTP_SUPPORT_SERVICE}
    listed = {tuple(item["path"]) for item in answer["answer"]["listed"]}
    assert (tree.POSTGRES_SERVICE,) not in listed, listed
    snap = inventory.snapshot(cli, endpoint)
    names = [n for obj in snap["containers"] for n in inventory.object_names(obj)]
    assert not [n for n in names if n.startswith(harness.selector_prefix(run_id))]
