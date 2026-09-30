"""Allowlisted task binding and run through the `ExecutionPort` (L.RB-4.3; B3-C14, B3-E1, B3-E5,
V-11, V-13, WR-ENV-3, WR-ENV-16, WR-CANCEL-5, OQ-18). STUB-PROVEN against the mise-shaped stub
(D-1, D-19); the real mise and Gradle are unverified.

`TaskRunner(resolver, execution, projects)` runs only what a project declares: a `TaskDeclaration`
whose `argv[0]` is a bare tool name and whose other entries are literals (a `./`-prefixed entry
names a path under the project's directory and is bound to its absolute path). Binding a task
(`bind`) does three things and no other:

1. resolves the task's tool through the `ToolchainResolver` (exact pin, never a search-path
   fallback): an `Unresolved` is returned as such, so nothing starts;
2. sets `argv[0]` to the resolved absolute executable (B3-C14: `argv[0] == command.resolved
   .executable`, else the call raises before any machine call);
3. builds the environment from empty plus the project's allowlist; the port adds only its scrubbed
   `PATH`, closes stdin and keeps every process in the run's group.

A task that is not declared, a tool named as a path, `mise` itself (or a shim), or a `./` path that
leaves the project directory is refused by raising `NotAllowlisted` BEFORE any machine call: the
resolver is not asked, nothing runs (a caller violation, B3-E1). A task is never run through the
backing tool (`mise exec`, `mise run`, a shim: WR-ENV-3).

`run(project, task, ticket, ...)` returns the port's `(Confirmation, ExecutionResult | None)`:
`NOT_APPLIED(<code>)` with the tool as `identity` when the tool is unresolved (`TOOLCHAIN_MISSING`
is human-actionable, J-5a: the node ends BLOCKED with V-11.1's action; nothing installs it, OQ-18),
and, for a Gradle-shaped command, when its self-provisioning is not disabled by declared
configuration or its wrapper-declared distribution is absent. A started task records its identity
first, as the `toolchain.task_start` evidence event: the resolved executable and version, the
project and task and (Gradle-shaped) the wrapper-declared distribution; a task that did not start
records none.

**Gradle-shaped task (B3-C14, decided).** `resolved` is the JDK the resolver gives and `argv[0]` its
absolute path, never the wrapper script; the argv runs `org.gradle.wrapper.GradleWrapperMain` from
the project's wrapper jar. `policy(command)` is `DISABLED_BY_CONFIGURATION` and `PREVENTED` only
when the argv carries `--no-daemon` and `-Porg.gradle.java.installations.auto-download=false`;
without either the policy is `BLOCKS` and `run` returns `NOT_APPLIED(TOOLCHAIN_MISSING)` (the step
blocks: WR-ENV-16). Before starting, the wrapper-declared distribution (`distributionUrl` in
`gradle-wrapper.properties` next to the jar) must be present in the project's
`distribution_store` (a directory holding one entry per distribution); absent, `run` returns
`NOT_APPLIED(TOOLCHAIN_MISSING)` and nothing starts. Gradle itself is absent and stays unproven.

The runner holds no `ToolchainProvisioning` and imports none (`provisioning.py` is a sibling
nothing here reaches): a task cannot install, refresh or download a toolchain.

The adapter imports only the standard library and `trestle.workflow` (BFD-47).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

from trestle.workflow.ports import (
    BoundCommand,
    CatalogEntry,
    ExecutionPolicy,
    ExecutionPort,
    ExecutionResult,
    Helpers,
    Resolved,
    SelfProvisioning,
    ToolchainResolver,
    Unresolved,
)
from trestle.workflow.services import AttemptTicket
from trestle.workflow.values import (
    CancelSignal,
    Confirmation,
    ConfirmationStatus,
    EvidenceSink,
    StopCause,
)

TOOLCHAIN_MISSING: Final = "execution.toolchain_missing"  # V-11
TASK_START: Final = "toolchain.task_start"  # the evidence event a started task records
WRAPPER_MAIN: Final = "org.gradle.wrapper.GradleWrapperMain"
NO_DAEMON: Final = "--no-daemon"
AUTO_DOWNLOAD_OFF: Final = "-Porg.gradle.java.installations.auto-download=false"
PROJECT_PATH_PREFIX: Final = "./"
WRAPPER_PROPERTIES: Final = "gradle-wrapper.properties"
BACKING_TOOL: Final = "mise"
RUN_BOUND: Final = timedelta(minutes=10)  # executor-chosen: a task's own deadline when none given


class NotAllowlisted(ValueError):
    """The caller asked for something the catalog does not allow: raised before any machine call."""


@dataclass(frozen=True, slots=True)
class TaskDeclaration:
    """One allowlisted task: `argv[0]` a bare tool name, the rest literals (`./x`: project path)."""

    id: str
    argv: tuple[str, ...]
    reports_tests: bool = False


@dataclass(frozen=True, slots=True)
class ProjectTasks:
    """What the runner knows of one catalog project: where it lives, its tasks, the environment a
    task is given (built from empty plus exactly this) and where a wrapper distribution is looked
    for."""

    directory: str
    tasks: Mapping[str, TaskDeclaration]
    environment: Mapping[str, str] = field(default_factory=dict)
    distribution_store: str | None = None


class _NeverCancel:
    @property
    def requested(self) -> bool:
        return False

    def cause(self) -> StopCause | None:
        return None

    def wait(self, timeout: timedelta) -> bool:
        return False


def is_gradle_shaped(argv: tuple[str, ...]) -> bool:
    return WRAPPER_MAIN in argv


def wrapper_distribution(argv: tuple[str, ...]) -> str | None:
    """The distribution the project's wrapper declares (`distributionUrl` beside the jar named by
    `-classpath`), as its file name without `.zip`, or `None` when it cannot be read."""
    if "-classpath" not in argv[:-1]:
        return None
    properties = Path(argv[argv.index("-classpath") + 1]).parent / WRAPPER_PROPERTIES
    try:
        lines = properties.read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError):
        return None
    for line in lines:
        key, _, value = line.partition("=")
        if key.strip() == "distributionUrl":
            return value.strip().replace("\\:", ":").rsplit("/", 1)[-1].removesuffix(".zip") or None
    return None


def _refuse(project: str, task: str, why: str) -> NotAllowlisted:
    return NotAllowlisted(f"{project}/{task}: {why}")


class TaskRunner:
    """Binds and runs the allowlisted tasks of the projects it was given."""

    def __init__(
        self,
        resolver: ToolchainResolver,
        execution: ExecutionPort,
        projects: Mapping[CatalogEntry, ProjectTasks],
        *,
        evidence: EvidenceSink | None = None,
        cancel: CancelSignal | None = None,
    ) -> None:
        self.resolver = resolver
        self.execution = execution
        self.projects = dict(projects)
        self.evidence = evidence
        self.cancel: CancelSignal = _NeverCancel() if cancel is None else cancel
        for name, project in self.projects.items():
            if not os.path.isabs(project.directory):
                raise ValueError(f"the directory of project {name} must be an absolute path")

    # -- binding

    def bind(self, project: CatalogEntry, task: str) -> BoundCommand | Unresolved:
        """The bound command of an allowlisted task, or the `Unresolved` of its tool. Raises
        `NotAllowlisted` (before the resolver is asked) for anything the project does not allow."""
        declared = self.projects.get(project)
        entry = None if declared is None else declared.tasks.get(task)
        if declared is None or entry is None or not entry.argv:
            raise _refuse(project, task, "not an allowlisted task")
        tool, *rest = entry.argv
        if not tool or "/" in tool or tool == BACKING_TOOL or tool.startswith(f"{BACKING_TOOL}-"):
            raise _refuse(project, task, f"{tool!r} is not a bare tool name the toolchain resolves")
        arguments = [self._argument(declared, project, task, a) for a in rest]
        resolved = self.resolver.resolve(project, tool)
        if isinstance(resolved, Unresolved):
            return resolved
        return BoundCommand(
            task=f"{project}/{task}",
            argv=(resolved.executable, *arguments),
            environment=dict(declared.environment),
            resolved=resolved,
            reports_tests=entry.reports_tests,
        )

    def _argument(self, declared: ProjectTasks, project: str, task: str, argument: str) -> str:
        if not argument.startswith(PROJECT_PATH_PREFIX):
            return argument
        root = os.path.realpath(declared.directory)
        bound = os.path.realpath(os.path.join(root, argument[len(PROJECT_PATH_PREFIX) :]))
        if os.path.commonpath([root, bound]) != root:
            raise _refuse(project, task, f"{argument!r} leaves the project directory")
        return bound

    # -- policy

    def policy(self, command: BoundCommand) -> ExecutionPolicy:
        """Pure. A Gradle-shaped command is `DISABLED_BY_CONFIGURATION` + `PREVENTED` when its argv
        turns the daemon and the toolchain auto-download off, `BLOCKS` otherwise; any other command
        is the execution port's own policy."""
        if not is_gradle_shaped(command.argv):
            return self.execution.policy(command)
        if NO_DAEMON in command.argv and AUTO_DOWNLOAD_OFF in command.argv:
            return ExecutionPolicy(
                SelfProvisioning.DISABLED_BY_CONFIGURATION, Helpers.PREVENTED, None
            )
        return ExecutionPolicy(SelfProvisioning.BLOCKS, Helpers.PREVENTED, None)

    # -- run

    def run(
        self,
        project: CatalogEntry,
        task: str,
        ticket: AttemptTicket,
        *,
        until: datetime | None = None,
    ) -> tuple[Confirmation, ExecutionResult | None]:
        """Run one allowlisted task once under `ticket`: the port's `(Confirmation,
        ExecutionResult | None)`; `NOT_APPLIED(TOOLCHAIN_MISSING)` (identity: the tool) when the
        tool cannot be resolved or a Gradle-shaped task would fetch, before anything starts."""
        bound = self.bind(project, task)
        if isinstance(bound, Unresolved):
            return Confirmation(ConfirmationStatus.NOT_APPLIED, bound.code, bound.tool), None
        distribution: str | None = None
        if is_gradle_shaped(bound.argv):
            blocked = self._gradle_block(project, task, bound)
            if blocked is not None:
                return blocked, None
            distribution = wrapper_distribution(bound.argv)
        self._record(project, task, bound.resolved, distribution)
        deadline = until if until is not None else datetime.now(UTC) + RUN_BOUND
        return self.execution.run(bound, ticket, self.cancel, deadline)

    def _gradle_block(self, project: str, task: str, bound: BoundCommand) -> Confirmation | None:
        """The refusal of a Gradle-shaped command that could fetch, or `None` when it cannot."""
        subject = self.projects[project].tasks[task].argv[0]  # the tool, as V-11.1 names it
        blocked = Confirmation(ConfirmationStatus.NOT_APPLIED, TOOLCHAIN_MISSING, subject)
        if self.policy(bound).self_provisioning is not SelfProvisioning.DISABLED_BY_CONFIGURATION:
            return blocked
        declared = wrapper_distribution(bound.argv)
        store = self.projects[project].distribution_store
        if declared is None or store is None or not (Path(store) / declared).exists():
            return blocked
        return None

    def _record(
        self, project: str, task: str, resolved: Resolved, distribution: str | None
    ) -> None:
        if self.evidence is None:
            return
        fields: dict[str, str | None] = {
            "project": project,
            "task": task,
            "executable": resolved.executable,
            "reported_version": resolved.reported_version,
            "distribution": distribution,
        }
        self.evidence.event(TASK_START, fields)
