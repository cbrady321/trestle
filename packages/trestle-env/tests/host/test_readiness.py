"""HTTP readiness on real Docker: the dependent starts after the declared response (L.RB-2.1).

DOCKER, venue HOST (`docker_host`; `python -m tests.proof.host.docker_gate run`, F-PX3): the
published `reference_env` runs through the control surface on the operator's docker at the gate's
endpoint, on the pinned MC-B-10 images (nginx serves the declared `/health` response). The
operator's catalog lists one test whose node needs both backends. The fact is read from the
finalized run's lane: the supporting node's readiness pass precedes the dependent's first entry.
Its CI twin is `tests/twin/test_readiness_twin.py`."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from tests.proof import records
from tests.tree import hostpath
from trestle.common.types import RunView
from trestle.server.main import Kernel, create_kernel
from twin.twin_engine import TEST_NODE, catalog_file

from trestle_env import schema, tree
from trestle_env.plugins import _bind, reference_env

pytestmark = pytest.mark.docker_host

PLUGINS = Path(reference_env.__file__).resolve().parent
ENV = "ref-readiness"


@pytest.fixture
def kernel(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Kernel:
    docker = shutil.which("docker")  # the operator's path, named here; the adapter never searches
    assert docker is not None, "the host-docker gate runs with a docker CLI (preflight)"
    monkeypatch.setenv(_bind.DOCKER_PATH_ENV, docker)
    monkeypatch.setenv("TRESTLE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    # the dependent: a catalog test's node needs both backends' readiness (it runs nothing here)
    monkeypatch.setenv(tree.CATALOG_ENV, str(catalog_file(tmp_path)))
    for role in ("POSTGRES", "HTTP_SUPPORT"):
        assert f"{_bind.IMAGE_ENV_PREFIX}{role}" in os.environ, "the gate exports the image pins"
    built = create_kernel(home=tmp_path / "home", plugin_dirs=[PLUGINS], skip_recovery=True)
    built.registry.refresh()
    return built


def containers_named(run_id: str) -> list[str]:
    """The containers of this run still on the engine (`docker ps -a` by run-scoped selector)."""
    cli = shutil.which("docker")
    assert cli is not None
    argv = [cli]
    endpoint = os.environ.get(_bind.ENDPOINT_ENV)
    if endpoint:
        argv += ["--host", endpoint]
    listed = subprocess.run(  # noqa: S603 - read-only listing by the test
        [*argv, "ps", "-a", "--format", "{{.Names}}", "--filter", f"name=trwr-{run_id}-"],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    return listed.stdout.split()


@pytest.mark.proves("WR-VERIFY-2", "WR-VERIFY-2:b-host-ordering", "B", "B", "DOCKER", "HOST")
@pytest.mark.proves(
    "WR-ENV-10", "WR-ENV-10:readiness-authoritative-http", "B", "B", "DOCKER", "HOST"
)
def test_dependent_starts_after_http_readiness_pass(kernel: Kernel) -> None:
    view = hostpath.run_tree_via_host(kernel, "reference_env", {schema.ENV_ARG: ENV})
    assert isinstance(view, RunView), view
    assert view.state == "succeeded", view
    (run_dir,) = sorted(p for p in (kernel.home / "runs").glob("*/*") if p.is_dir())
    lane = records.lane_rows(run_dir)
    assert not lane.problems and not lane.torn, lane.problems
    rows: list[dict[str, Any]] = [row.entry for row in lane.rows]

    def first(path: str) -> int:
        return next(n for n, row in enumerate(rows) if row.get("path") == path)

    def end(path: str) -> int:
        return next(
            n for n, row in enumerate(rows) if row.get("path") == path and row["class"] == "end"
        )

    # the supporting service's readiness pass (the declared response) precedes the dependent's start
    assert rows[end(tree.HTTP_SUPPORT_PATH)]["condition"] == "satisfied"
    assert end(tree.HTTP_SUPPORT_PATH) < first(TEST_NODE)
    assert end(tree.POSTGRES_SERVICE) < first(TEST_NODE)
    assert rows[end(TEST_NODE)]["condition"] == "satisfied"
    # released with the run: no container of this run is left on the engine
    assert containers_named(view.run_id) == []
