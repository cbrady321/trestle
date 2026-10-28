"""HOST: on real Docker the started set equals the Compose closure, and a stack given only its
Compose definition runs (L.RB-1.4; WR-ENV-1; DOCKER · HOST, run only by the host-docker gate).

The reference plugin is bound to the operator's docker at the gate's endpoint with the pinned
images (`docker_gate run` exports both) and to the Compose definition `closure_case.COMPOSE`
through `TRESTLE_ENV_COMPOSE_FILE`, so the plugin derives the closure with the REAL resolver
(`docker compose config`, L.NW-2.7) before any effect. The expected closure is derived in the test
by that same real resolver; the started set is read from the answer and the lane (`closure_case`),
and nothing of the run remains on the engine afterwards (the gate's record diff covers the rest).
Each node's twin is `twin/test_closure_host_twin.py` (same node names, the fake binding).

Known bound (B-COMPOSE-RETURN gap 1, lane plan item 4): the execution port hands the resolver only
a 512-byte console excerpt, and `docker compose config --format json` of this two-service
definition is longer, so until that contract is decided the real resolver refuses it
(`COMPOSE_DEFINITION_INVALID`) and these nodes fail at their first assertion, naming it.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from tests.proof.host.docker_gate import inventory
from trestle_packs.container import bind
from trestle_packs.process.command import CommandPort
from twin import closure_case, harness

pytestmark = pytest.mark.docker_host


def _engine() -> tuple[str, str | None]:
    cli = shutil.which("docker")  # the operator's docker; the adapter itself never searches
    assert cli is not None, "the host-docker gate runs with a docker CLI (preflight)"
    return cli, os.environ.get("TRESTLE_DOCKER_ENDPOINT")


def real_closure(selected: list[str]) -> frozenset[str]:
    cli, endpoint = _engine()
    resolver = bind(
        cli, endpoint, CommandPort(), compose_projects={closure_case.PROJECT: closure_case.COMPOSE}
    ).compose
    derived = resolver.closure(closure_case.PROJECT, frozenset(selected))
    assert not hasattr(derived, "code"), f"the real resolver refused the definition: {derived}"
    return frozenset(derived.services)


def run(tmp_path: Path, services: list[str] | None) -> frozenset:
    cli, endpoint = _engine()
    environ = closure_case.environ(harness.host_environ())
    with harness.reference_host(tmp_path / "home", environ) as host:
        answer = closure_case.call(host, "closure-host", services)
        assert answer["state"] == "succeeded", answer
        assert answer["answer"]["outcome"] == "passed", answer
        run_id = answer["run_id"]
        entries = harness.lane(harness.run_dir(host, run_id))
    got = closure_case.started(answer, entries, run_id)
    snap = inventory.snapshot(cli, endpoint)
    names = [n for obj in snap["containers"] for n in inventory.object_names(obj)]
    assert not [n for n in names if n.startswith(harness.selector_prefix(run_id))]
    return got


@pytest.mark.proves("WR-ENV-1", "WR-ENV-1:started-set-equals-closure", "B", "B", "DOCKER", "HOST")
def test_started_set_equals_closure(tmp_path: Path) -> None:
    expected = real_closure(closure_case.SELECTED)
    assert run(tmp_path, closure_case.SELECTED) == expected


@pytest.mark.proves("WR-ENV-1", "WR-ENV-1:compose-file-only-works", "B", "B", "DOCKER", "HOST")
def test_compose_file_only_stack_works(tmp_path: Path) -> None:
    expected = real_closure(closure_case.every_service())
    assert run(tmp_path, None) == expected
