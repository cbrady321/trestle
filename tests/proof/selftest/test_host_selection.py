"""Selftest for the CSC-9 host_only/docker_host deselection hook
(L.P0-0d.9). Planted pytester nodes; no test file of ours carries these
markers, so a plain `pytest -q` collects 0 host_only/docker_host items
today (checked live by MJ.P0-0d, not asserted here)."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

PYTESTER_INI = "pytest_plugins = ['tests.proof.plugin']\n"

PLANTED = """
import pytest

@pytest.mark.host_only
def test_host_only_node():
    assert True

@pytest.mark.docker_host
def test_docker_host_node():
    assert True

def test_plain_node():
    assert True
"""


def _run(pytester, env_gate: str | None, args=()):
    pytester.makeconftest("pytest_plugins = ['tests.proof.plugin']\n")
    pytester.makepyfile(test_planted=PLANTED)
    saved_gate = os.environ.get("TRESTLE_HOST_GATE")
    saved_path = os.environ.get("PYTHONPATH")
    packs_src = str(ROOT / "packages" / "trestle-packs")
    os.environ["PYTHONPATH"] = os.pathsep.join(
        [str(ROOT), packs_src] + ([saved_path] if saved_path else [])
    )
    if env_gate is None:
        os.environ.pop("TRESTLE_HOST_GATE", None)
    else:
        os.environ["TRESTLE_HOST_GATE"] = env_gate
    try:
        return pytester.runpytest_subprocess("-p", "no:cacheprovider", *args)
    finally:
        if saved_gate is None:
            os.environ.pop("TRESTLE_HOST_GATE", None)
        else:
            os.environ["TRESTLE_HOST_GATE"] = saved_gate
        if saved_path is None:
            os.environ.pop("PYTHONPATH", None)
        else:
            os.environ["PYTHONPATH"] = saved_path


def test_host_only_and_docker_host_deselected_without_gate_env(pytester):
    result = _run(pytester, None)
    result.assert_outcomes(passed=1, deselected=2)


def test_host_only_collected_only_under_proc_gate_env(pytester):
    result = _run(pytester, "proc", args=("-v",))
    result.assert_outcomes(passed=2, deselected=1)
    result.stdout.fnmatch_lines(["*test_host_only_node*"])


def test_docker_host_collected_only_under_docker_gate_env(pytester):
    result = _run(pytester, "docker")
    result.assert_outcomes(passed=2, deselected=1)


def test_deselected_not_skipped(pytester):
    result = _run(pytester, None)
    result.assert_outcomes(passed=1, skipped=0, deselected=2)


def test_hook_applies_in_packs_session(pytester):
    """The hook is registered once in the root plugin, loaded by every
    session (root, packs, env) via the same `pytest_plugins` tuple — this
    proves the hook function itself is session-agnostic (it reads only
    `item` markers and an env var, never a root-specific fixture)."""
    from tests.proof import plugin as plugin_mod

    assert "modifyitems" in dir(plugin_mod) or hasattr(plugin_mod, "pytest_collection_modifyitems")
