"""L.RB-12.3 twins: a server killed mid-run, recovered on restart, on the fake binding (STUB · CI).

The same `tree_variants` run (`cancel` mode: its fake step holds), the same kill and restart as
`host/test_restart_docker.py`. The fake engine's `ArgvRelease` descriptors name a release wrapper
over `twin/state_docker.py` (the same state file), which the MCP host's own `config.toml` lists in
`[operator] release_executables`, so recovery's plan-rank sweep releases the created containers
from the record alone. Registers only `@stub-twin` labels.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from tests.core.spine import support
from tests.proof import ancestry, tolerances

from twin import fake_binding, harness, release, variants

FOUND = {"name": "trestle-found-restart", "state": "running", "port": 0, "environment": {}}


def _recovered(tmp_path: Path, fail: str | None) -> tuple[dict[str, Any], Path, Path, int, Path]:
    state = tmp_path / "engine.json"
    state.write_text(json.dumps({"containers": [FOUND]}), encoding="utf-8")
    script, log = release.wrapper(tmp_path / "bin", release.fake_real(state), fail)
    home = tmp_path / "home"
    release.operator_config(home, [str(script)])
    environ = {**harness.twin_environ(state), fake_binding.DOCKER_ENV: str(script)}
    with variants.variants_host(home, environ) as host:
        run_id = variants.start(host, env="restart-twin", mode="cancel")
        try:
            assert support.wait_until(
                lambda: (
                    variants.both_created(host, run_id)
                    and variants.identities_recorded(host, run_id)
                ),
                tolerances.JOIN_WAIT_S * 3,
            ), "both containers were never created"
            before = len(release.calls(log))
            view = variants.kill_and_recover(host, run_id)
        finally:
            ancestry.reap(support.marked(run_id))
        run_dir = harness.run_dir(host, run_id)
    return view, state, log, before, run_dir


def _selectors(run_id: str) -> list[str]:
    return [harness.selector_prefix(run_id) + p for p in (variants.HELPER, variants.POSTGRES)]


@pytest.mark.stub_proven("WR-UNIT-5:b-server-restart@stub-twin")
@pytest.mark.stub_proven("WR-OWN-1:created-container-released@stub-twin")
def test_server_killed_restart_recovery_releases_created_container(tmp_path: Path) -> None:
    view, state, log, before, run_dir = _recovered(tmp_path, None)
    answer = view["answer"]
    assert answer["recovered"] is True and answer["root_stop"] == "restart", view
    assert answer["cleanup"]["unknown"] == 0 and answer["cleanup"]["released"] >= 2, answer
    assert variants.group_stop(run_dir)["confirmed_gone"] is True  # the owned process is gone
    names = [c["name"] for c in fake_binding.read_state(state)["containers"]]
    for selector in _selectors(view["run_id"]):
        assert selector not in names
        assert release.release_steps(log, selector, before) == ["ps", "stop", "rm", "ps"]
    assert FOUND["name"] in names  # the found container is untouched
    assert not [c for c in release.calls(log) if FOUND["name"] in " ".join(c["args"])]


def test_recovery_remove_failure_reports_unknown(tmp_path: Path) -> None:
    view, state, log, before, run_dir = _recovered(tmp_path, "remove")
    cleanup = view["answer"]["cleanup"]
    assert cleanup["unknown"] >= 1 and cleanup["clean"] is False, view
    rows = [r for r in _sweep_rows(run_dir) if r["disposition"] != "unknown"]
    assert not [r for r in rows if r["disposition"] in ("released", "nothing_created")]
    names = [c["name"] for c in fake_binding.read_state(state)["containers"]]
    for selector in _selectors(view["run_id"]):
        assert selector in names  # stopped, never removed: never reported released
        assert release.release_steps(log, selector, before)[:3] == ["ps", "stop", "rm (failed)"]


def _sweep_rows(run_dir: Path) -> list[dict[str, Any]]:
    from tests.proof import records

    return [r for r in records.ledger_rows(run_dir).rows if r.get("kind") == "sweep_disposition"]
