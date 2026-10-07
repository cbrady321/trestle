"""A never-ready service ends at its stage budget on real Docker (L.RB-2.2; B5.1, WR-ENV-9).

DOCKER, venue HOST (`docker_host`; `docker_gate run`, F-PX3): the published `reference_env` on the
operator's docker and the pinned images, with a WRONG password in the Postgres readiness exec
environment, so the authenticated `SELECT 1` can never pass. The run ends when the readiness
node's declared wait elapses: BLOCKED through the EXHAUSTED class with `POSTCONDITION_TIMEOUT`, the
human action and re-send present, `primary.path` naming the readiness stage and the failing
service. A found container the run never made is untouched. CI twin:
`tests/twin/test_stage_budget_twin.py`."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from trestle.common import clock
from trestle.common.plan import vocabulary
from trestle.common.types import RunView
from trestle.server.main import Kernel, create_kernel

from trestle_env import schema, stages, tree
from trestle_env.plugins import _bind, reference_env

pytestmark = pytest.mark.docker_host

PLUGINS = Path(reference_env.__file__).resolve().parent
PLANTED = Path(__file__).with_name("planted_password.py")
FIXTURE = "trestle.proof.fixture=stage-budget"


def docker(*args: str) -> str:
    cli = shutil.which("docker")  # the operator's path, named here; the adapter never searches
    assert cli is not None, "the host-docker gate runs with a docker CLI (preflight)"
    host = ["--host", os.environ[_bind.ENDPOINT_ENV]] if os.environ.get(_bind.ENDPOINT_ENV) else []
    done = subprocess.run(  # noqa: S603 - the test's own fixture container
        [cli, *host, *args], capture_output=True, text=True, check=True, timeout=120
    )
    return done.stdout.strip()


@pytest.fixture
def found() -> Iterator[str]:
    """A container the run never made, labelled as a proof fixture (CSC-10): its id and running
    state must be the same after the run."""
    name = f"trestle-stage-budget-{uuid.uuid4().hex[:8]}"
    image = os.environ["TRESTLE_IMAGE_ALPINE"]  # `<repo>@sha256:<hex>`, exported by the gate
    docker(
        "run", "-d", "--pull", "never", "--label", FIXTURE, "--name", name, image, "sleep", "600"
    )
    try:
        yield name
    finally:
        subprocess.run(  # noqa: S603
            [shutil.which("docker") or "docker", "rm", "-f", name], capture_output=True, check=False
        )


@pytest.fixture
def kernel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Kernel:
    docker_path = shutil.which("docker")
    assert docker_path is not None
    monkeypatch.setenv(_bind.DOCKER_PATH_ENV, docker_path)
    monkeypatch.setenv(_bind.PORTS_ENV, f"{PLANTED}:wrong_password")
    monkeypatch.setenv("TRESTLE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    built = create_kernel(home=tmp_path / "home", plugin_dirs=[PLUGINS], skip_recovery=True)
    built.registry.refresh()
    return built


def state_of(name: str) -> tuple[str, str]:
    """`(container id, running)` of a container: what a run must leave as it found it."""
    return tuple(docker("inspect", "--format", "{{.Id}} {{.State.Running}}", name).split())  # type: ignore[return-value]


@pytest.mark.proves("WR-ENV-9", "B5.1", "B", "B", "DOCKER", "HOST")
@pytest.mark.proves(
    "WR-ENV-9", "WR-ENV-9:stage-k-named-found-untouched", "B", "B", "DOCKER", "HOST"
)
def test_readiness_ends_at_stage_budget_names_stage_and_service(kernel: Kernel, found: str) -> None:
    before = state_of(found)
    wait_ms = int((tree.DEADLINE_S + clock.finalization_margin) * 1000)
    started = time.monotonic()
    view = kernel.control.run(
        plugin="reference_env",
        args={schema.ENV_ARG: f"ref-{uuid.uuid4().hex[:8]}"},
        wait_ms=wait_ms,
        completion="terminal",
    )
    elapsed = time.monotonic() - started
    assert isinstance(view, RunView), view
    answer = view.answer
    assert answer is not None and answer["outcome"] == "blocked", answer
    primary = answer["primary"]
    assert primary["code"] == vocabulary.POSTCONDITION_TIMEOUT
    assert primary["node_class"] == vocabulary.NodeClass.EXHAUSTED.value  # B4-T2 row 8
    assert primary["human_action"] and primary["resend"] is not None  # B4-C5
    named = stages.failure_at("/".join(primary["path"]), primary["code"])
    assert named == stages.StageFailure(stages.Stage.READINESS, "postgres", primary["code"])
    # it ended when the readiness node's declared wait elapsed: the supporting node's whole budget,
    # then the wait, then the margin (MC-09) and nothing else
    assert elapsed <= tree.LEAF_BUDGET_S + tree.READY_WAIT_S + clock.finalization_margin
    # found untouched, and nothing of this run left: the failure's cleanup touched only owned ones
    assert state_of(found) == before
    assert (
        docker("ps", "-a", "--format", "{{.Names}}", "--filter", f"name=trwr-{view.run_id}-") == ""
    )
