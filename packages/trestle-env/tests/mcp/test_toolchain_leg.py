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

import json
import stat
import sys
from pathlib import Path
from typing import Any

import pytest
from twin import fake_binding, harness

from trestle_env import schema, tree
from trestle_env.catalog import REFERENCE_PATH
from trestle_env.plugins import _bind

SEAM = Path(__file__).with_name("toolchain_seam.py")
STUB_MISE = Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "stubs" / "stub_mise.py"
TEST_ID = "demo-version"
NODE = f"test.{TEST_ID}"
TOOLCHAIN_MISSING = "execution.toolchain_missing"
TASK_START = "toolchain.task_start"
PYTHON = "3.12.4"


def script(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


class World:
    """Everything the stub mise and the resolver need, on disk under `base`."""

    def __init__(self, base: Path, *, installed: bool) -> None:
        install = base / "installs" / "python" / PYTHON
        script(
            install / "bin" / "python",
            f"#!{sys.executable}\nimport sys\n"
            f"print('Python {PYTHON}' if sys.argv[1:] == ['--version'] else '')\n",
        )
        self.mise = script(
            base / "bin" / "stub_mise",
            f"#!{sys.executable}\nimport runpy, sys\nsys.argv = ['stub_mise.py', *sys.argv[1:]]\n"
            f"runpy.run_path({str(STUB_MISE)!r}, run_name='__main__')\n",
        )
        self.config = base / "world" / "mise.json"
        self.log = base / "world" / "mise.log"
        self.config.parent.mkdir(parents=True)
        entry = {
            "version": PYTHON,
            "requested_version": "3.12",
            "install_path": str(install),
            "installed": installed,
            "active": True,
            "source": {"type": "stub_mise.toml", "path": "world"},
        }
        self.config.write_text(json.dumps({"mode": "normal", "tools": {"python": entry}}), "utf-8")
        self.projects = base / "projects"
        (self.projects / "demo-py").mkdir(parents=True)
        self.catalog = base / "catalog.json"
        data = json.loads(REFERENCE_PATH.read_text(encoding="utf-8"))
        data["tests"] = [{"id": TEST_ID, "project": "demo-py", "task": "version"}]
        self.catalog.write_text(json.dumps(data), "utf-8")

    def environ(self, state: Path) -> dict[str, str | None]:
        return {
            **harness.twin_environ(state, f"{SEAM}:stub_toolchain_ports"),
            tree.CATALOG_ENV: str(self.catalog),
            _bind.PROJECTS_DIR_ENV: str(self.projects),
            "TESTKIT_MISE": str(self.mise),
            "TESTKIT_MISE_CONFIG": str(self.config),
            "TESTKIT_MISE_LOG": str(self.log),
        }

    def mise_calls(self) -> list[dict[str, Any]]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines() if line]


def call(host: Any) -> dict[str, Any]:
    """One `run(reference_env, tests=[demo-version], completion="terminal")`."""
    answer = host.call(
        "run",
        {
            "plugin": harness.PLUGIN_NAME,
            "args": {schema.ENV_ARG: "toolchain-leg", schema.TESTS_ARG: [TEST_ID]},
            "wait_ms": harness.tolerances.HARNESS_WAIT_MS,
            "completion": "terminal",
        },
    )
    assert isinstance(answer, dict), answer
    return answer


@pytest.mark.proves("WR-PROOF-3", "WR-PROOF-3:b-toolchain-label", "B", "B", "MCP+STUB", "CI")
def test_pin_satisfied_passes_with_identity(tmp_path: Path) -> None:
    world = World(tmp_path / "world-dir", installed=True)
    with harness.reference_host(tmp_path / "home", world.environ(tmp_path / "engine.json")) as host:
        answer = call(host)
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
        answer = call(host)
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
