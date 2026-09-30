"""L.RB-3.2: a foreign occupant and an unhealthy found resource end blocked, never signalled,
adopted or repaired (DOCKER+PROC · HOST; run only by the host-docker gate).

Before the run the TEST starts one profile of `fixtures/foreign/compose.yaml`: a container named
exactly as the tree's logical system (`postgres`), fixture-labelled, so the run finds it.

* `[container]` - another image (alpine): the reuse proof cannot prove its identity, so
  `backend.postgres` ends INCOMPATIBLE with `FOUND_INCOMPATIBLE` (J-12);
* unhealthy - the reference identity and the pinned Postgres, with a password the reference
  readiness call does not present: identity and configuration proven, readiness never passes,
  so it ends INCOMPATIBLE with `FOUND_UNHEALTHY` (J-14).

Both are classed BLOCKED (B4-T2 row 10) with V-11.1's human action. The found container is never
signalled (its main process keeps its pid and start time, it keeps running and never restarted),
never adopted (no ticket of the run names it) and never repaired; the run creates nothing for
`backend.postgres`. One MCP `run` per case, through the reference plugin on the operator's docker.
The CI twins are `twin/test_foreign_twin.py` (same node names).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from tests.proof import tolerances
from tests.proof.host.docker_gate import inventory
from twin import harness

from trestle_env import tree

pytestmark = pytest.mark.docker_host

COMPOSE = Path(__file__).resolve().parents[1] / "fixtures" / "foreign" / "compose.yaml"
FOUND = tree.POSTGRES_SERVICE


def _docker(*args: str) -> subprocess.CompletedProcess[str]:
    cli = shutil.which("docker")
    assert cli is not None, "the host-docker gate runs with a docker CLI (preflight)"
    return subprocess.run(  # noqa: S603 - the test's own fixture, the operator's docker
        inventory.docker_cmd(cli, os.environ.get("TRESTLE_DOCKER_ENDPOINT"), *args),
        capture_output=True,
        text=True,
        env=inventory.docker_env(),
        stdin=subprocess.DEVNULL,
        timeout=tolerances.JOIN_WAIT_S * 6,
        check=False,
    )


def _found_state() -> dict[str, Any]:
    shown = _docker("inspect", "--format", "{{json .}}", FOUND)
    assert shown.returncode == 0, shown.stderr
    data = json.loads(shown.stdout)
    state = data["State"]
    return {
        "id": data["Id"],
        "image": data["Config"]["Image"],
        "pid": state["Pid"],
        "started_at": state["StartedAt"],
        "running": state["Running"],
        "restarts": data["RestartCount"],
    }


@contextmanager
def _planted(profile: str) -> Iterator[dict[str, Any]]:
    """Start the fixture profile; yield the found container's state; remove it afterwards."""
    assert _docker("inspect", FOUND).returncode != 0, "a container named postgres already exists"
    project = f"trestle-foreign-{uuid.uuid4().hex[:8]}"
    compose = ("compose", "-p", project, "-f", str(COMPOSE), "--profile", profile)
    up = _docker(*compose, "up", "-d")
    assert up.returncode == 0, up.stderr
    try:
        yield _found_state()
    finally:
        _docker(*compose, "down")


@pytest.fixture
def foreign() -> Iterator[dict[str, Any]]:
    with _planted("foreign") as state:
        yield state


@pytest.fixture
def unhealthy() -> Iterator[dict[str, Any]]:
    with _planted("unhealthy") as state:
        yield state


def _blocked(answer: dict[str, Any], code: str) -> None:
    body = answer["answer"]
    assert body["outcome"] == "blocked", answer
    primary = body["primary"]
    assert primary["path"] == [tree.POSTGRES_UNIT], primary
    assert primary["code"] == code, primary
    assert primary["condition"] == "incompatible" or primary["node_class"] == "blocked", primary
    assert primary["human_action"], primary  # V-11.1: what a person does, then re-send


def _run_blocked(tmp_path: Path, env: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    with harness.reference_host(tmp_path / "home", harness.host_environ()) as host:
        answer = harness.run_terminal(host, env=env)
        entries = harness.lane(harness.run_dir(host, answer["run_id"]))
    return answer, entries


def _untouched(before: dict[str, Any], entries: list[dict[str, Any]]) -> None:
    assert _found_state() == before  # same container, same process, still running, no restart
    assert not [e for e in entries if e.get("path") == tree.POSTGRES_UNIT and e["class"] == "issue"]
    assert not [e for e in entries if e.get("identity") == FOUND]  # nothing acted on it


@pytest.mark.proves(
    "WR-OWN-7", "WR-OWN-7:b-foreign-blocked-not-killed", "B", "B", "DOCKER+PROC", "HOST"
)
@pytest.mark.parametrize("occupant", ["container"])
def test_foreign_occupant_blocked_never_signalled(
    occupant: str, foreign: dict[str, Any], tmp_path: Path
) -> None:
    answer, entries = _run_blocked(tmp_path, env="foreign-host")
    _blocked(answer, "execution.found_incompatible")
    _untouched(foreign, entries)


@pytest.mark.proves(
    "WR-OWN-10", "WR-OWN-10:b-unhealthy-found-blocked", "B", "B", "DOCKER+PROC", "HOST"
)
def test_unhealthy_found_blocked_not_repaired(unhealthy: dict[str, Any], tmp_path: Path) -> None:
    answer, entries = _run_blocked(tmp_path, env="unhealthy-host")
    _blocked(answer, "execution.found_unhealthy")
    _untouched(unhealthy, entries)
