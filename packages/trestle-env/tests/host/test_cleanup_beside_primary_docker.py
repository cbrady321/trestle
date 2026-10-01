"""L.RB-12.5: the WR-OWN-6 Docker-inventory half (DOCKER · HOST; run only by the host-docker gate).

A passed `tree_variants` run whose container release is made to fail: the run is bound to
`docker` = the release wrapper (`tests/fixtures/stubs/docker_stop_fails.py`) failing
`remove_argv`, and the MCP host's own `config.toml` allowlists that wrapper, so neither the loop's
release nor the host sweep can remove the containers. The primary class stays `passed`; the
cleanup disposition is written durably beside it before the terminal row (B4-C7, B2-C9 Post);
RunView.cleanup (MC-32) reports the containers `unknown`, not released and never clean; and the
Docker inventory agrees: each run-scoped container is present (stopped). The gate's housekeeping
then removes that run-attributable residue and records it (CSC-10). The CI twin is
`twin/test_cleanup_beside_primary_docker_twin.py`.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest
from tests.proof.host.docker_gate import inventory
from twin import harness, release, variants

pytestmark = pytest.mark.docker_host


@pytest.mark.proves("WR-OWN-6", "WR-OWN-6:docker-inventory", "core", "B", "DOCKER", "HOST")
def test_cleanup_failure_beside_primary_matches_docker_inventory(tmp_path: Path) -> None:
    cli = shutil.which("docker")
    assert cli is not None, "the host-docker gate runs with a docker CLI (preflight)"
    script, _log = release.wrapper(tmp_path / "bin", [cli], "remove")
    home = tmp_path / "home"
    release.operator_config(home, [str(script)])
    environ = {**harness.host_environ(), "TRESTLE_DOCKER_PATH": str(script)}
    with variants.variants_host(home, environ) as host:
        answer = variants.run_terminal(host, env="own6-host", mode="passed", note=False)
        run_dir = harness.run_dir(host, answer["run_id"])
    assert answer["answer"]["outcome"] == "passed", answer  # the primary is unchanged
    cleanup = answer["answer"]["cleanup"]
    assert cleanup["unknown"] >= 1 and cleanup["clean"] is False, cleanup
    assert variants.cleanup_beside_primary(run_dir)
    snap = inventory.snapshot(cli, os.environ.get("TRESTLE_DOCKER_ENDPOINT"))
    names = [n for obj in snap["containers"] for n in inventory.object_names(obj)]
    prefix = harness.selector_prefix(answer["run_id"])
    assert sorted(n for n in names if n.startswith(prefix)) == [
        prefix + variants.HELPER,
        prefix + variants.POSTGRES,
    ]  # present, as the answer's unknown says; the gate's housekeeping removes them
