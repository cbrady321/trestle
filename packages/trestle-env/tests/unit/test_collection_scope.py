"""Fixture suites are never collected by the root or the env session (L.RB-0.1)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
FIXTURE_DIRS = ("packages/trestle-env/tests/fixtures/", "packages/trestle-packs/tests/fixtures/")
ROOT_SESSION = ("-c", "pyproject.toml", "--rootdir", ".")
PROBE = "packages/trestle-env/tests/fixtures/collection-probe/test_probe_never_collected.py"


def _collect(*args: str, gate: bool = False) -> subprocess.CompletedProcess[str]:
    env = {
        k: v for k, v in os.environ.items() if k not in ("TRESTLE_PROOF_GATE", "TRESTLE_HOST_GATE")
    }
    if gate:
        env["TRESTLE_HOST_GATE"] = ""
    return subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def _node_ids(proc: subprocess.CompletedProcess[str]) -> list[str]:
    return [line.strip() for line in proc.stdout.splitlines() if "::" in line]


def test_no_node_collected_under_fixtures() -> None:
    # Non-vacuous: the probe IS collected when named explicitly (an explicit path is not ignored).
    explicit = _collect(*ROOT_SESSION, PROBE)
    assert any(n.startswith(PROBE) for n in _node_ids(explicit)), explicit.stdout[-500:]

    env_session = _collect(*ROOT_SESSION, "packages/trestle-env/tests")
    core_session = _collect()
    for proc in (env_session, core_session):
        assert proc.returncode == 0, proc.stdout[-1500:] + proc.stderr[-500:]
        ids = _node_ids(proc)
        assert ids
        assert not [n for n in ids if n.startswith(FIXTURE_DIRS)]
    assert any(n.startswith("packages/trestle-env/tests/unit/") for n in _node_ids(env_session))
    assert any(n.startswith("packages/trestle-env/tests/unit/") for n in _node_ids(core_session))

    # The host-gate deselection (CSC-9) still reaches env tests through testpaths: 0 collected.
    host = _collect("-m", "host_only or docker_host", gate=True)
    assert host.returncode in (0, 5), host.stdout[-1500:]
    assert _node_ids(host) == []
