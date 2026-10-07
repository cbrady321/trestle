"""L.RB-4.3: an allowlisted task runs through the `ExecutionPort` as a bound command, and nothing
installs, downloads or upgrades a toolchain as a side effect (B3-C14, B3-E1, B3-E5, WR-ENV-3,
WR-ENV-16, B8.4, OQ-18).

Everything real but the tools: the real `MiseToolchainResolver` over the stub `stub_mise`, the real
`CommandPort` running real children, and installs whose executables are shims onto the stub tool
(`stub_tool`, which logs how it was run) and the fetch logger (which records and refuses a
download). The STUB-PROVEN labels stand for the real mise and Gradle, both unverified (D-1, D-19).
"""

from __future__ import annotations

import ast
import json
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from tests.proof.suites.ports import core
from tests.single.workflow.joinkit import (
    NO_READINGS,
    NOT_APPLIED,
    RECORDED,
    clock,
    record,
    terms,
    ticket,
)
from trestle.workflow.declarations import EffectFacetClass, Lifetime, Repeat
from trestle.workflow.join import join
from trestle.workflow.ports import (
    ExecutionClass,
    Helpers,
    InRunGroup,
    SelfProvisioning,
    Unresolved,
)
from trestle.workflow.services import AttemptTicket
from trestle.workflow.values import Condition, ConfirmationStatus, Lineage, NodePath

from trestle_packs.process.command import CommandPort
from trestle_packs.toolchain import MiseToolchainResolver
from trestle_packs.toolchain import tasks as tasks_module
from trestle_packs.toolchain.provisioning import StubToolchainProvisioning
from trestle_packs.toolchain.tasks import (
    AUTO_DOWNLOAD_OFF,
    NO_DAEMON,
    TASK_START,
    WRAPPER_MAIN,
    NotAllowlisted,
    ProjectTasks,
    TaskDeclaration,
    TaskRunner,
)

from . import rig

STUBS = rig.REPO / "tests" / "fixtures" / "stubs"
STUB_TOOL = STUBS / "stub_tool.py"
DISTRIBUTION = "gradle-8.5-bin"
PY, JVM = "demo-py", "demo-jvm"
TICKET = AttemptTicket(
    lineage=Lineage("r_toolchain_0002", NodePath(("task",))),
    effect="task",
    facet=EffectFacetClass.EVENT,
    attempt=1,
    repeat=Repeat.SAFE,
    lifetime=Lifetime.RUN,
    release=InRunGroup(),
    remedy=None,
)

# the reference catalog's demo-jvm task (packages/trestle-env/.../reference.json)
GRADLE_ARGV = (
    "java",
    "-classpath",
    "./gradle/wrapper/gradle-wrapper.jar",
    WRAPPER_MAIN,
    NO_DAEMON,
    AUTO_DOWNLOAD_OFF,
    "build",
)
NOT_STRIPPED = tuple(a for a in GRADLE_ARGV if a != AUTO_DOWNLOAD_OFF)


def shim(version_line: str, mode: tuple[str, ...] = ()) -> str:
    """An install's executable: `--version` answers itself, anything else is the stub tool."""
    return (
        f"#!{sys.executable}\nimport runpy, sys\n"
        f"if sys.argv[1:] == ['--version']:\n    print({version_line!r})\n    sys.exit(0)\n"
        f"sys.argv = ['stub_tool.py', *{list(mode)!r}, *sys.argv[1:]]\n"
        f"runpy.run_path({str(STUB_TOOL)!r}, run_name='__main__')\n"
    )


class Events:
    def __init__(self) -> None:
        self.items: list[tuple[str, Mapping[str, Any]]] = []

    def event(self, kind: str, fields: Mapping[str, Any]) -> None:
        self.items.append((kind, dict(fields)))


@dataclass
class Rig:
    world: rig.World
    runner: TaskRunner
    events: Events
    tool_log: Path
    fetch_log: Path
    store: Path
    provisioning: StubToolchainProvisioning = field(default_factory=StubToolchainProvisioning)

    def tool_runs(self) -> list[dict[str, Any]]:
        if not self.tool_log.exists():
            return []
        return [json.loads(line) for line in self.tool_log.read_text().splitlines()]

    def fetches(self) -> list[dict[str, Any]]:
        if not self.fetch_log.exists():
            return []
        return [json.loads(line) for line in self.fetch_log.read_text().splitlines()]

    def snapshot(self) -> tuple[dict[str, str], dict[str, str]]:
        return core.tree_state(self.world.envelope), core.tree_state(self.world.installs)


def assert_no_fetch(fetches: list[dict[str, Any]]) -> None:
    assert fetches == [], f"the task tried to fetch: {fetches}"


def build(tmp_path: Path, gradle_argv: tuple[str, ...] = GRADLE_ARGV) -> Rig:
    world = rig.World(tmp_path / "world")
    world.install("java", "21.0.2", "21")
    rig.write_executable(
        world.installs / "java" / "21.0.2" / "bin" / "java",
        shim("openjdk 21.0.2", ("--mode", "wrapper-fetch")),
    )
    rig.write_executable(
        world.installs / "python" / "3.12.4" / "bin" / "python", shim("Python 3.12.4")
    )
    world.write_config()
    tool_log, fetch_log = tmp_path / "tool.log", tmp_path / "fetch.log"
    store = tmp_path / "dists"
    (store / DISTRIBUTION).mkdir(parents=True)
    py_dir, jvm_dir = tmp_path / "proj-py", tmp_path / "proj-jvm"
    (py_dir).mkdir()
    (py_dir / "data.txt").write_text("data")
    wrapper = jvm_dir / "gradle" / "wrapper"
    wrapper.mkdir(parents=True)
    (wrapper / "gradle-wrapper.jar").write_bytes(b"jar")
    (wrapper / "gradle-wrapper.properties").write_text(
        f"distributionUrl=https\\://services.gradle.org/distributions/{DISTRIBUTION}.zip\n"
    )
    environment = {
        "STUB_TOOL_LOG": str(tool_log),
        "STUB_FETCH_LOG": str(fetch_log),
        "STUB_TOOL_DIST_STORE": str(store),
    }
    projects = {
        PY: ProjectTasks(
            str(py_dir),
            {
                "probe": TaskDeclaration("probe", ("python", "--flag", "./data.txt")),
                "escape": TaskDeclaration("escape", ("python", "./../secret")),
                "by-path": TaskDeclaration("by-path", ("/usr/bin/env", "true")),
                "backing": TaskDeclaration("backing", ("mise", "exec", "--", "python")),
                "absent": TaskDeclaration("absent", ("node", "--version")),
            },
            environment,
        ),
        JVM: ProjectTasks(
            str(jvm_dir),
            {
                "build": TaskDeclaration("build", gradle_argv),
                "stripped": TaskDeclaration("stripped", NOT_STRIPPED),
            },
            environment,
            str(store),
        ),
    }
    mise_env = {"STUB_MISE_CONFIG": str(world.config), "STUB_MISE_LOG": str(world.log)}
    port = CommandPort()
    resolver = MiseToolchainResolver(
        world.stub, port, {PY: mise_env, JVM: mise_env}, envelope=world.envelope
    )
    events = Events()
    runner = TaskRunner(resolver, port, projects, evidence=events)
    return Rig(world, runner, events, tool_log, fetch_log, store)


@pytest.fixture
def env(tmp_path: Path) -> Rig:
    return build(tmp_path)


LABEL = "WR-ENV-3:allowlisted-noninteractive-identity"


@pytest.mark.proves(
    "WR-ENV-3", "WR-ENV-3:never-via-backing-tool-orchestration", "B", "B", "STUB+PROC", "CI"
)
@pytest.mark.parametrize("task", ["escape", "by-path", "backing", "nope"])
def test_non_allowlisted_task_refused(env: Rig, task: str) -> None:
    before = env.snapshot()
    with pytest.raises(NotAllowlisted):
        env.runner.run(PY, task, TICKET)
    with pytest.raises(NotAllowlisted):
        env.runner.run("no-such-project", "probe", TICKET)
    assert env.snapshot() == before  # no envelope or install change
    assert not env.world.log.exists()  # the resolver was not even asked: no machine call
    assert env.tool_runs() == [] and env.fetches() == []
    assert env.events.items == []
    assert env.provisioning.calls == []


@pytest.mark.proves("WR-ENV-3", LABEL, "B", "B", "STUB+PROC", "CI")
def test_stub_sees_no_tty_no_transport_bytes(env: Rig) -> None:
    confirmation, result = env.runner.run(PY, "probe", TICKET)
    assert confirmation.status is ConfirmationStatus.APPLIED
    assert result is not None and ExecutionClass(result.classification) is ExecutionClass.PASSED
    (run,) = env.tool_runs()
    assert run["isatty"] == {"stdin": False, "stdout": False, "stderr": False}
    assert (run["stdin"], run["stdin_bytes"]) == ("eof", 0)  # closed: nothing waits, nothing read
    allowed = set(env.runner.projects[PY].environment)
    assert allowed <= set(run["env"])
    extra = set(run["env"]) - allowed - {"PATH", "__CF_USER_TEXT_ENCODING", "LC_CTYPE"}
    assert extra == set(), f"the task saw an environment beyond the allowlist: {extra}"
    executable = str(env.world.installs / "python" / "3.12.4" / "bin" / "python")
    project = env.runner.projects[PY].directory
    assert run["argv"] == ["--flag", str(Path(project).resolve() / "data.txt")]
    assert run["env"]["PATH"].split(":")[0] == str(Path(executable).parent)


@pytest.mark.proves("WR-ENV-3", LABEL, "B", "B", "STUB+PROC", "CI")
def test_identity_recorded(env: Rig) -> None:
    env.runner.run(PY, "probe", TICKET)
    env.runner.run(JVM, "build", TICKET)
    python, java = (fields for kind, fields in env.events.items if kind == TASK_START)
    assert python["executable"] == str(env.world.installs / "python" / "3.12.4" / "bin" / "python")
    assert python["reported_version"] == "3.12.4" and python["distribution"] is None
    assert (python["project"], python["task"]) == (PY, "probe")
    assert java["executable"] == str(env.world.installs / "java" / "21.0.2" / "bin" / "java")
    assert java["reported_version"] == "21.0.2" and java["distribution"] == DISTRIBUTION


@pytest.mark.proves(
    "WR-ENV-3", "WR-ENV-3:never-via-backing-tool-orchestration", "B", "B", "STUB+PROC", "CI"
)
def test_task_never_runs_through_the_backing_tool(env: Rig) -> None:
    env.runner.run(PY, "probe", TICKET)
    env.runner.run(JVM, "build", TICKET)
    calls = [json.loads(line)["argv"] for line in env.world.log.read_text().splitlines()]
    assert calls and all(c[:3] == ["ls", "--current", "--json"] for c in calls), calls
    assert not any(c[0] in ("exec", "run", "x", "install") for c in calls)
    for command in (env.runner.bind(PY, "probe"), env.runner.bind(JVM, "build")):
        assert not isinstance(command, Unresolved)
        assert command.argv[0] == command.resolved.executable
        assert Path(command.argv[0]).name in ("python", "java")  # never `mise`, never a shim dir


@pytest.mark.proves("WR-ENV-16", "B8.4", "B", "B", "STUB", "CI")
@pytest.mark.proves("WR-ENV-16", "WR-ENV-16:no-install-side-effect", "B", "B", "STUB+PROC", "CI")
def test_task_never_reaches_install_path(env: Rig) -> None:
    before = env.snapshot()
    env.runner.run(PY, "probe", TICKET)
    env.runner.run(JVM, "build", TICKET)
    env.runner.run(PY, "absent", TICKET)  # a missing tool: blocked, not installed
    assert env.snapshot() == before  # the envelope fs snapshot and the installs are equal
    assert_no_fetch(env.fetches())
    assert env.provisioning.calls == []  # provisioning was never called


def test_a_runner_holds_no_provisioning_port_and_imports_none(env: Rig) -> None:
    source = Path(tasks_module.__file__).read_text(encoding="utf-8")
    imported = [
        n.module or "" for n in ast.walk(ast.parse(source)) if isinstance(n, ast.ImportFrom)
    ]
    assert not any("provisioning" in name for name in imported)
    assert not any(hasattr(value, "install_pinned") for value in vars(env.runner).values())
    stub = StubToolchainProvisioning()  # gated: installs are not permitted by default (OQ-18)
    refused = stub.install_pinned(PY, "python", TICKET_SAFE_START)
    assert refused.status is ConfirmationStatus.NOT_APPLIED
    assert refused.code == "execution.toolchain_missing"


TICKET_SAFE_START = AttemptTicket(
    TICKET.lineage, "install", EffectFacetClass.SAFE_START, 1, Repeat.SAFE, Lifetime.DURABLE,
    InRunGroup(), None,
)  # fmt: skip


@pytest.mark.proves(
    "WR-ENV-16", "WR-ENV-16:jvm-wrapper-fetch-disabled-or-blocked", "B", "B", "STUB+PROC", "CI"
)
def test_gradle_argv0_is_resolved_jdk_running_wrapper_main(env: Rig) -> None:
    command = env.runner.bind(JVM, "build")
    assert not isinstance(command, Unresolved)
    java = str(env.world.installs / "java" / "21.0.2" / "bin" / "java")
    jar = str(
        Path(env.runner.projects[JVM].directory).resolve() / "gradle/wrapper/gradle-wrapper.jar"
    )
    assert command.argv[0] == java == command.resolved.executable
    assert command.argv[1:4] == ("-classpath", jar, WRAPPER_MAIN)
    assert NO_DAEMON in command.argv and AUTO_DOWNLOAD_OFF in command.argv
    assert not any(a.endswith(("gradlew", "gradlew.bat")) for a in command.argv)  # never the script
    policy = env.runner.policy(command)
    assert policy.self_provisioning is SelfProvisioning.DISABLED_BY_CONFIGURATION
    assert policy.helpers is Helpers.PREVENTED
    confirmation, result = env.runner.run(JVM, "build", TICKET)
    assert confirmation.status is ConfirmationStatus.APPLIED
    assert result is not None and ExecutionClass(result.classification) is ExecutionClass.PASSED
    (run,) = env.tool_runs()
    assert run["mode"] == "wrapper-fetch"
    assert_no_fetch(env.fetches())


@pytest.mark.proves(
    "WR-ENV-16", "WR-ENV-16:jvm-wrapper-fetch-disabled-or-blocked", "B", "B", "STUB+PROC", "CI"
)
def test_missing_distribution_not_applied_toolchain_missing_blocked(env: Rig) -> None:
    (env.store / DISTRIBUTION).rmdir()
    confirmation, result = env.runner.run(JVM, "build", TICKET)
    assert confirmation.status is ConfirmationStatus.NOT_APPLIED
    assert confirmation.code == "execution.toolchain_missing" and result is None
    assert [k for k, _ in env.events.items if k == TASK_START] == []  # no task-start record
    assert env.tool_runs() == []  # nothing started
    assert_no_fetch(env.fetches())
    verdict = join(
        terms(completion=RECORDED),
        None,
        record(
            tickets=(
                ticket(status=NOT_APPLIED, code=confirmation.code, identity=confirmation.identity),
            )
        ),
        NO_READINGS,
        clock(0),
    )
    assert verdict.condition is Condition.BLOCKED  # J-5a
    assert verdict.code == "execution.toolchain_missing"
    assert verdict.human_action is not None and "java" in verdict.human_action  # V-11.1's action


@pytest.mark.proves(
    "WR-ENV-16", "WR-ENV-16:jvm-wrapper-fetch-disabled-or-blocked", "B", "B", "STUB+PROC", "CI"
)
def test_a_task_without_the_auto_download_property_blocks_before_it_starts(env: Rig) -> None:
    command = env.runner.bind(JVM, "stripped")
    assert not isinstance(command, Unresolved)
    assert env.runner.policy(command).self_provisioning is SelfProvisioning.BLOCKS
    confirmation, result = env.runner.run(JVM, "stripped", TICKET)
    assert (confirmation.status, confirmation.code) == (
        ConfirmationStatus.NOT_APPLIED,
        "execution.toolchain_missing",
    )
    assert result is None and env.tool_runs() == [] and env.events.items == []


def test_an_unresolved_tool_is_not_applied_and_starts_nothing(env: Rig) -> None:
    confirmation, result = env.runner.run(PY, "absent", TICKET)
    assert confirmation.status is ConfirmationStatus.NOT_APPLIED
    assert (confirmation.code, confirmation.identity) == ("execution.toolchain_missing", "node")
    assert result is None and env.tool_runs() == [] and env.events.items == []


@pytest.mark.proves(
    "WR-ENV-16", "WR-ENV-16:jvm-wrapper-fetch-disabled-or-blocked", "B", "B", "STUB+PROC", "CI"
)
@pytest.mark.parametrize("kind", ["distribution", "toolchain"])
def test_planted_omission_makes_no_fetch_check_fail(tmp_path: Path, kind: str) -> None:
    """Past the presence check the wrapper stub really fetches: the distribution absent, or the
    auto-download property stripped. The same no-fetch assertion the other cases use raises."""
    planted = build(tmp_path, GRADLE_ARGV if kind == "distribution" else NOT_STRIPPED)
    bound = planted.runner.bind(JVM, "build")
    assert not isinstance(bound, Unresolved)
    if kind == "distribution":
        (planted.store / DISTRIBUTION).rmdir()
    # skip the runner's checks: hand the bound command straight to the port
    CommandPort().run(
        bound, TICKET, tasks_module._NeverCancel(), datetime.now(UTC) + timedelta(seconds=60)
    )
    fetches = planted.fetches()
    assert [f["fetch"] for f in fetches] == [kind]
    with pytest.raises(AssertionError):
        assert_no_fetch(fetches)
