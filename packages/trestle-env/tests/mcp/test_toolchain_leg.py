"""The toolchain leg of the reference tree through one MCP call (L.RB-4.5; B4.6, WR-ENV-15).

The operator's catalog lists a test (`demo-version`: the `version` task of `demo-py`, which pins
Python 3.12), so the tree has a `test.demo-version` node. The published `reference_env` runs on the
healthy fake engine of the twins and the REAL toolchain resolver and task runner over the
mise-shaped STUB (`tests/fixtures/stubs/stub_mise.py`): the stub stands for mise, so the leg is
STUB-PROVEN and the real tool stays unverified (D-1, OPEN-MISE-HOST, docs/environment.md).

* pin satisfied: the run passes and the toolchain identity of the task (the resolved executable and
  its version) is in the run's evidence, recorded before the task started;
* pin unsatisfied: the node ends BLOCKED with TOOLCHAIN_MISSING and V-11.1's human action, and no
  task-start record, no ticket and no process exist (nothing installs the tool, OQ-18)."""

from __future__ import annotations

from pathlib import Path

import pytest
from twin import fake_binding, harness
from twin.toolchain_world import SEAM, World, call

from trestle_env import schema

TEST_ID = "demo-version"
NODE = f"test.{TEST_ID}"
TOOLCHAIN_MISSING = "execution.toolchain_missing"
TASK_START = "toolchain.task_start"
PYTHON = "3.12.4"


@pytest.mark.proves("WR-PROOF-3", "WR-PROOF-3:b-toolchain-label", "B", "B", "MCP+STUB", "CI")
def test_pin_satisfied_passes_with_identity(tmp_path: Path) -> None:
    world = World(tmp_path / "world-dir", installed=True)
    with harness.reference_host(tmp_path / "home", world.environ(tmp_path / "engine.json")) as host:
        answer = call(host, TEST_ID)
        events = harness.events(harness.run_dir(host, answer["run_id"]))
    assert answer["answer"]["outcome"] == "passed", answer["answer"]
    started = [e for e in events if e.get("kind") == TASK_START]
    assert len(started) == 1, events
    fields = started[0]["payload"]
    # the identity of what ran: the resolved absolute executable and the version it reports
    assert fields["executable"].endswith(f"python/{PYTHON}/bin/python"), fields
    assert fields["reported_version"] == PYTHON and fields["project"] == "demo-py"
    assert fields["task"] == "version"
    # the toolchain manager was asked one question, `ls --current --json`, never `exec`/`run`
    calls = world.mise_calls()
    assert calls and not [c for c in calls if c["forbidden"]]
    assert all(c["argv"][:3] == ["ls", "--current", "--json"] for c in calls)


@pytest.mark.proves("WR-ENV-15", "B4.6", "B", "B", "MCP+STUB", "CI")
@pytest.mark.stub_proven("WR-ENV-15:workflow-blocked-before-task")
def test_pin_unsatisfied_blocked_before_task_start(tmp_path: Path) -> None:
    world = World(tmp_path / "world-dir", installed=False)
    with harness.reference_host(tmp_path / "home", world.environ(tmp_path / "engine.json")) as host:
        answer = call(host, TEST_ID)
        run_dir = harness.run_dir(host, answer["run_id"])
        events = harness.events(run_dir)
        entries = harness.lane(run_dir)
    body = answer["answer"]
    assert body["outcome"] == "blocked", body
    primary = body["primary"]
    assert primary["code"] == TOOLCHAIN_MISSING and primary["path"] == [NODE], primary
    assert primary["human_action"] and "python" in primary["human_action"]  # V-11.1: name the tool
    assert primary["resend"] == "succeeds_after_action"
    # before any task start: no identity record, no ticket for the node, no process ran the task
    assert not [e for e in events if e.get("kind") == TASK_START]
    assert not [e for e in entries if e.get("path") == NODE and e["class"] in ("issue",)]
    assert fake_binding.read_state(tmp_path / "engine.json") is not None


@pytest.mark.proves("WR-PROOF-4", "WR-PROOF-4:b-real-mise-suite", "B", "B", "STUB", "CI")
@pytest.mark.gated_on("OPEN-MISE-HOST")
def test_real_mise_suite_is_gated_the_leg_runs_on_the_stub_only() -> None:
    """No case here binds a real mise: the leg is proven against the stub, and the real tool's
    behaviour stays unverified until OPEN-MISE-HOST is decided (the docs say so)."""
    seam = SEAM.read_text(encoding="utf-8")
    assert "TESTKIT_MISE" in seam and "which(" not in seam and 'environ["PATH"]' not in seam
    assert (
        "OPEN-MISE-HOST"
        in (Path(__file__).resolve().parents[4] / "docs" / "environment.md").read_text()
    )


@pytest.mark.proves(
    "WR-ENV-3", "WR-ENV-3:host-wide-toolchain-serialization", "B", "B", "MCP+STUB", "CI"
)
@pytest.mark.gated_on("OQ-26")
def test_host_wide_toolchain_serialization_is_assumed_per_environment(tmp_path: Path) -> None:
    """OQ-26 (host-wide credential and toolchain serialization) is undecided: the leg assumes the
    per-environment lease reading, so two runs on DIFFERENT environments both resolve and run."""
    world = World(tmp_path / "world-dir", installed=True)
    with harness.reference_host(tmp_path / "home", world.environ(tmp_path / "engine.json")) as host:
        outcomes = []
        for env in ("toolchain-a", "toolchain-b"):
            answer = host.call(
                "run",
                {
                    "plugin": harness.PLUGIN_NAME,
                    "args": {schema.ENV_ARG: env, schema.TESTS_ARG: [TEST_ID]},
                    "wait_ms": harness.tolerances.HARNESS_WAIT_MS,
                    "completion": "terminal",
                },
            )
            outcomes.append(answer["answer"]["outcome"])
    assert outcomes == ["passed", "passed"]
