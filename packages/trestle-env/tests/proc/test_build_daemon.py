"""L.RB-10.2: the build-daemon pattern on a Gradle-shaped stub (WR-CANCEL-7; B3-C3, B3-C14, B6.2).
PROC, venue BOTH: no Docker, no real Gradle (D-19: JVM/Gradle is STUB-PROVEN only).

`tests/fixtures/stubs/stub_gradle.py` stands for the resolved `java` running the project's wrapper
main class. It starts a build daemon (a process in its own session that outlives the build) unless
its argv carries `--no-daemon`, and it can start an in-group helper or an escaping one on request.
Three claims, each read from the process table or the record, never from console text:

* the declared configuration PREVENTS the daemon: the task's policy is `DISABLED_BY_CONFIGURATION` +
  `PREVENTED`, and the run starts none; the same stub without the flag does start one (the check has
  teeth), and a task that lost the flag blocks before anything starts;
* an in-group helper is ended within the stop bound after a cancel (MC-13's ancestry of the run is
  empty), in a real run whose helper the LOOP launched;
* a helper outside Gradle's daemon machinery is a DISCLOSED boundary: a task that declares one has
  policy `DISCLOSED`, its release descriptor says `helpers_disclosed`, the stub's escaping helper
  really leaves the run's group and session, and `docs/environment.md` names it as a boundary.

What the stub stands for, real Gradle, is unverified and out of scope (D-19).
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from tests.core.spine import support
from tests.proof import ancestry, harness, tolerances
from tests.single.workflow.proc import procrun
from trestle.common import clock
from trestle.workflow.declarations import EffectFacetClass, Lifetime, Repeat
from trestle.workflow.ports import (
    BoundCommand,
    ExecutionClass,
    Helpers,
    InRunGroup,
    Resolved,
    SelfProvisioning,
)
from trestle.workflow.services import AttemptTicket
from trestle.workflow.values import ConfirmationStatus, Lineage, NodePath
from trestle_packs.process.command import CommandPort
from trestle_packs.toolchain.tasks import (
    AUTO_DOWNLOAD_OFF,
    NO_DAEMON,
    WRAPPER_MAIN,
    ProjectTasks,
    TaskDeclaration,
    TaskRunner,
)

from trestle_env.plugins._tasks import TaskExecution

REPO = Path(__file__).resolve().parents[4]
STUB = REPO / "tests" / "fixtures" / "stubs" / "stub_gradle.py"
PLUGIN_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "plugins"
DOCS = REPO / "docs" / "environment.md"
DISTRIBUTION = "gradle-8.5-bin"
PROJECT = "demo-jvm"
TASK = "assemble"
DISCLOSURE = "The build's language server is started by the tool itself, outside Gradle's daemon."
TICKET = AttemptTicket(
    lineage=Lineage("r_daemon_0001", NodePath(("task",))),
    effect="task",
    facet=EffectFacetClass.EVENT,
    attempt=1,
    repeat=Repeat.SAFE,
    lifetime=Lifetime.RUN,
    release=InRunGroup(),
    remedy=None,
)
PROVES = "STUB+PROC+INSPECT"


class JavaResolver:
    """The toolchain manager's answer for `java`: the test's interpreter (the stub is a script)."""

    def resolve(self, project: str, tool: str) -> Resolved:
        assert tool == "java"
        return Resolved(sys.executable, "21.0.2", "21", "adoption")


def gradle_argv(*flags: str) -> tuple[str, ...]:
    return (
        "java",
        str(STUB),
        "-classpath",
        "./gradle/wrapper/gradle-wrapper.jar",
        WRAPPER_MAIN,
        *flags,
        "assemble",
    )


def runner_over(
    tmp_path: Path,
    argv: tuple[str, ...],
    *,
    helper: str = "",
    disclosure: str = "",
) -> tuple[TaskRunner, Path]:
    """A runner for one Gradle-shaped task of a project whose wrapper and distribution are in place
    (so nothing would fetch); the stub logs to the returned path."""
    project = tmp_path / "project"
    wrapper = project / "gradle" / "wrapper"
    wrapper.mkdir(parents=True)
    (wrapper / "gradle-wrapper.jar").write_bytes(b"")
    (wrapper / "gradle-wrapper.properties").write_text(
        f"distributionUrl=https\\://example.invalid/{DISTRIBUTION}.zip\n", encoding="utf-8"
    )
    store = tmp_path / "distributions"
    (store / DISTRIBUTION).mkdir(parents=True)
    log = tmp_path / "stub.log"
    environment = {
        "STUB_GRADLE_LOG": str(log),
        "STUB_GRADLE_TAG": str(tmp_path / "tag-daemon-test"),
        "STUB_GRADLE_HELPER": helper,
    }
    task = TaskDeclaration(TASK, argv, helper_disclosure=disclosure)
    projects = {PROJECT: ProjectTasks(str(project), {TASK: task}, environment, str(store))}
    return TaskRunner(JavaResolver(), CommandPort(), projects), log


def events(log: Path) -> list[dict[str, Any]]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text().splitlines() if line]


def kill_helpers(log: Path) -> None:
    """Whatever a test's stub left running (a daemon or a helper is a process of its own)."""
    for event in events(log):
        pid = event.get("helper_pid")
        if isinstance(pid, int):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.mark.proves("WR-CANCEL-7", "WR-CANCEL-7:prevented-by-config", "B", "B", PROVES, "BOTH")
def test_daemon_prevented_by_declared_config(tmp_path: Path) -> None:
    declared = (NO_DAEMON, AUTO_DOWNLOAD_OFF)
    runner, log = runner_over(tmp_path / "declared", gradle_argv(*declared))
    try:
        bound = runner.bind(PROJECT, TASK)
        assert isinstance(bound, BoundCommand)
        policy = runner.policy(bound)
        assert policy.self_provisioning is SelfProvisioning.DISABLED_BY_CONFIGURATION
        assert policy.helpers is Helpers.PREVENTED and policy.disclosure is None
        confirmation, result = runner.run(PROJECT, TASK, TICKET)
        assert confirmation.status is ConfirmationStatus.APPLIED
        assert result is not None and result.classification is ExecutionClass.PASSED
        ran = [e["event"] for e in events(log)]
        assert ran == ["start", "done"]  # no daemon was started
    finally:
        kill_helpers(log)

    # the check has teeth: the same stub WITHOUT the declared flag does start a daemon
    unflagged, log2 = runner_over(tmp_path / "unflagged", gradle_argv())
    try:
        bound = unflagged.bind(PROJECT, TASK)
        assert isinstance(bound, BoundCommand)
        assert unflagged.policy(bound).self_provisioning is SelfProvisioning.BLOCKS
        # the runner refuses it before anything starts (no daemon, no process)...
        confirmation, result = unflagged.run(PROJECT, TASK, TICKET)
        assert confirmation.status is ConfirmationStatus.NOT_APPLIED and result is None
        assert events(log2) == []
        # ...so the daemon is shown by running the stub itself, as a build without the flag would
        env = {**os.environ, **runner_environment(unflagged)}
        subprocess.run([sys.executable, str(STUB), "-classpath", "x.jar", WRAPPER_MAIN], env=env)
        started = [e for e in events(log2) if e["event"] == "daemon_started"]
        assert len(started) == 1 and alive(started[0]["helper_pid"])
    finally:
        kill_helpers(log2)


def runner_environment(runner: TaskRunner) -> dict[str, str]:
    return dict(runner.projects[PROJECT].environment)


@pytest.fixture
def short_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    procrun.short_stop(monkeypatch)


@pytest.mark.proves("WR-CANCEL-7", "WR-CANCEL-7:contained-within-bound", "B", "B", PROVES, "BOTH")
@pytest.mark.proves("WR-CANCEL-7", "B6.2", "B", "B", "STUB+PROC", "BOTH")
def test_in_group_helper_ended_within_bound_after_cancel(tmp_path: Path, short_stop: None) -> None:
    plugins = tmp_path / "plugins"
    plugins.mkdir()
    (plugins / "gradle_leaf.py").write_text(
        (PLUGIN_DIR / "gradle_leaf.py").read_text(encoding="utf-8"), encoding="utf-8"
    )
    kernel = harness.fresh_kernel(plugin_dirs=[plugins], home=tmp_path / "home")
    tag = procrun.tag_for(tmp_path, "gradle")
    log = tmp_path / "stub.log"
    with support.reaping(tag):
        order = support.admit_order(
            kernel,
            "gradle_leaf",
            {
                "env": "e",
                "tag": tag,
                "log": str(log),
                "stub": str(STUB),
                "helper": "ingroup",
                "seconds": tolerances.JOIN_WAIT_S * 6,
            },
        )
        run_dir = support.run_dir_of(kernel, order.run_id)
        thread = support.drive_in_thread(kernel, order)
        procrun.wait_for(lambda: len(support.marked(tag)) >= 1, "the in-group helper")
        helpers = support.marked(tag)
        (start,) = [e for e in events(log) if e["event"] == "start"]
        # the helper is in the build's own process group and session: the run's (V-2.3)
        assert {(p.pgid, p.sid) for p in helpers} == {(start["pgid"], start["sid"])}
        requested = time.time()
        kernel.control.cancel(order.run_id)
        thread.join(timeout=tolerances.JOIN_WAIT_S + clock.stop_bound)
        assert not thread.is_alive(), "conductor never returned"
        assert support.kinds(run_dir)[-1] == "cancelled"
        # the helper is absent from MC-13's ancestry of the run by the stop bound
        assert support.wait_until(
            lambda: not support.alive_marked(helpers, tag),
            clock.stop_bound + tolerances.PROC_WAIT_S,
        )
        assert (
            time.time() - requested
            <= clock.stop_bound + tolerances.PROC_WAIT_S + tolerances.JOIN_WAIT_S
        )
        (stop,) = support.rows_of(run_dir, "group_stop")
        assert stop["confirmed_gone"] is True


@pytest.mark.proves("WR-CANCEL-7", "WR-CANCEL-7:disclosed-boundary", "B", "B", PROVES, "BOTH")
def test_escaping_helper_named_in_docs(tmp_path: Path) -> None:
    # a task that declares a helper outside Gradle's daemon machinery is DISCLOSED, and its release
    # descriptor says so, so the answer can name the disclosure (B3-C3)
    argv = gradle_argv(NO_DAEMON, AUTO_DOWNLOAD_OFF)
    runner, log = runner_over(tmp_path / "declared", argv, helper="escaping", disclosure=DISCLOSURE)
    execution = TaskExecution(CommandPort(), runner)
    try:
        bound = runner.bind(PROJECT, TASK)
        assert isinstance(bound, BoundCommand)
        policy = runner.policy(bound)
        assert policy.helpers is Helpers.DISCLOSED and policy.disclosure == DISCLOSURE
        assert policy.self_provisioning is SelfProvisioning.DISABLED_BY_CONFIGURATION
        call = type("Call", (), {"arguments": {"command": bound}})()
        assert execution.release_descriptor(call) == InRunGroup(helpers_disclosed=True)  # type: ignore[arg-type]
        # the stub's escaping helper really leaves the run's group and session, and is reparented
        confirmation, _ = runner.run(PROJECT, TASK, TICKET)
        assert confirmation.status is ConfirmationStatus.APPLIED
        (start,) = [e for e in events(log) if e["event"] == "start"]
        (helper,) = [e for e in events(log) if e["event"] == "helper_started"]
        pid = helper["helper_pid"]
        assert support.wait_until(lambda: ancestry.snapshot() and alive(pid), 5)
        me = next(p for p in ancestry.snapshot() if p.pid == pid)
        assert me.pgid != start["pgid"] and me.sid != start["sid"] and me.ppid != start["pid"]
    finally:
        kill_helpers(log)
    # the boundary is named in the docs, where an operator reads it (RV-1)
    text = DOCS.read_text(encoding="utf-8")
    assert "Containment boundary" in text
    boundary = text.split("Containment boundary", 1)[1].lower()
    assert "outside gradle's daemon" in boundary and "not contained" in boundary
    assert "disclosed" in boundary and "d-19" in boundary
