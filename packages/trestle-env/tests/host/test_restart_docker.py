"""L.RB-12.3: a server killed mid-run and restarted; recovery's plan-rank sweep releases the created
containers from the record alone (DOCKER+PROC · HOST; run only by the host-docker gate).

The `tree_variants` run (`cancel` mode: its fake step holds once both containers exist) is bound
to `docker` = the release wrapper (`tests/fixtures/stubs/docker_stop_fails.py`) over the
operator's docker, and the MCP host's own `config.toml` lists that wrapper in
`[operator] release_executables` (V-10.1; no other configuration lists it). The server is SIGKILLed
with the run live and restarted on the same home: recovery (B2-C11) ends the run's process group
by its recorded identities and sweeps the record with no plugin code (B2-C9).

* released: each created container is absent afterwards and the answer reports it released; the
  wrapper's log shows the sweep's commands for it in order: observe (`ps`), stop, remove, observe;
  the run's group is confirmed gone (MC-13's owned process); a found container is untouched.
* remove failure: the wrapper fails `remove_argv`; the target is `unknown`, never `released` and
  never `nothing_created`, and the cleanup is never clean. The stopped container is left as
  run-attributable residue for the gate's housekeeping (CSC-10).

The CI twins are `twin/test_restart_docker_twin.py`.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from tests.core.spine import support
from tests.proof import ancestry, records, tolerances
from tests.proof.host.docker_gate import inventory
from twin import harness, release, variants

pytestmark = pytest.mark.docker_host

FIXTURE_LABEL = "trestle.proof.fixture=tree-variants"
KEEP_RUNNING = ("sh", "-c", "trap 'exit 0' TERM; while :; do sleep 1; done")


def _cli() -> str:
    cli = shutil.which("docker")
    assert cli is not None, "the host-docker gate runs with a docker CLI (preflight)"
    return cli


def _docker(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - the test's own fixture, the operator's docker
        inventory.docker_cmd(_cli(), os.environ.get("TRESTLE_DOCKER_ENDPOINT"), *args),
        capture_output=True,
        text=True,
        env=inventory.docker_env(),
        stdin=subprocess.DEVNULL,
        timeout=tolerances.JOIN_WAIT_S * 3,
        check=False,
    )


@pytest.fixture
def found() -> Iterator[str]:
    name = f"trestle-found-{uuid.uuid4().hex[:8]}"
    made = _docker(
        "run", "-d", "--pull", "never", "--name", name, "--label", FIXTURE_LABEL,
        os.environ["TRESTLE_IMAGE_ALPINE"], *KEEP_RUNNING,
    )  # fmt: skip
    assert made.returncode == 0, made.stderr
    try:
        yield name
    finally:
        _docker("rm", "-f", name)


def _found_state(name: str) -> tuple[str, bool]:
    shown = _docker("inspect", "--format", "{{json .}}", name)
    assert shown.returncode == 0, shown.stderr
    data = json.loads(shown.stdout)
    return str(data["Id"]), bool(data["State"]["Running"])


def _names() -> list[str]:
    listed = _docker("ps", "-a", "--format", "{{.Names}}")
    assert listed.returncode == 0, listed.stderr
    return listed.stdout.split()


def _recovered(tmp_path: Path, fail: str | None) -> tuple[dict[str, Any], Path, int, Path]:
    script, log = release.wrapper(tmp_path / "bin", [_cli()], fail)  # argv carries --host
    home = tmp_path / "home"
    release.operator_config(home, [str(script)])
    environ = {**harness.host_environ(), "TRESTLE_DOCKER_PATH": str(script)}
    with variants.variants_host(home, environ) as host:
        run_id = variants.start(host, env="restart-host", mode="cancel")
        try:
            assert support.wait_until(
                lambda: (
                    variants.both_created(host, run_id)
                    and variants.identities_recorded(host, run_id)
                ),
                tolerances.JOIN_WAIT_S * 6,
            ), "both containers were never created"
            before = len(release.calls(log))
            view = variants.kill_and_recover(host, run_id)
        finally:
            ancestry.reap(support.marked(run_id))
        run_dir = harness.run_dir(host, run_id)
    return view, log, before, run_dir


def _selectors(run_id: str) -> list[str]:
    return [harness.selector_prefix(run_id) + p for p in (variants.HELPER, variants.POSTGRES)]


@pytest.mark.proves("WR-UNIT-5", "WR-UNIT-5:b-server-restart", "B", "B", "DOCKER+PROC", "HOST")
@pytest.mark.proves(
    "WR-OWN-1", "WR-OWN-1:created-container-released", "core", "B", "DOCKER", "HOST"
)
@pytest.mark.proves("WR-UNIT-5", "B8.5", "B", "B", "DOCKER", "HOST")
def test_server_killed_restart_recovery_releases_created_container(
    found: str, tmp_path: Path
) -> None:
    before_found = _found_state(found)
    view, log, before, run_dir = _recovered(tmp_path, None)
    answer = view["answer"]
    assert answer["recovered"] is True and answer["root_stop"] == "restart", view
    assert answer["cleanup"]["unknown"] == 0 and answer["cleanup"]["released"] >= 2, answer
    assert variants.group_stop(run_dir)["confirmed_gone"] is True  # the owned process is gone
    names = _names()
    for selector in _selectors(view["run_id"]):
        assert selector not in names  # observed absent: released
        assert release.release_steps(log, selector, before) == ["ps", "stop", "rm", "ps"]
    assert _found_state(found) == before_found == (before_found[0], True)  # found untouched
    assert not [c for c in release.calls(log) if found in " ".join(c["args"])]


def test_recovery_remove_failure_reports_unknown(tmp_path: Path) -> None:
    view, log, before, run_dir = _recovered(tmp_path, "remove")
    cleanup = view["answer"]["cleanup"]
    assert cleanup["unknown"] >= 1 and cleanup["clean"] is False, view
    swept = [r for r in records.ledger_rows(run_dir).rows if r.get("kind") == "sweep_disposition"]
    assert swept and all(r["disposition"] == "unknown" for r in swept), swept
    names = _names()
    for selector in _selectors(view["run_id"]):
        assert selector in names  # stopped, never removed: residue for housekeeping (CSC-10)
        assert release.release_steps(log, selector, before)[:3] == ["ps", "stop", "rm (failed)"]
