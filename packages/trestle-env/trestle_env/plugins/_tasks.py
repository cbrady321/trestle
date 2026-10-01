"""The toolchain leg's execution port (L.RB-4.5; B3-C14, WR-ENV-3; composition root only).

The tree's `TaskUnit` issues an EVENT effect through `ExecutionPort` with the command it
resolved. `TaskExecution` is the port bound for that effect: the command's `task` is
`<project>/<task>` (or `NOTHING`), and a real task is run by the `TaskRunner`, which binds it AGAIN
from the catalog's allowlisted argv (never from the command the unit handed over), records the
toolchain identity before it starts and never reaches an install path. `NOTHING` (the test was not
requested) is a run of nothing: applied, passed, no process. The runner's own `NOT_APPLIED` (a tool
that no longer resolves, a Gradle-shaped task that would fetch) passes through unchanged, so the
join ends the node BLOCKED (J-5a).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from trestle.workflow.ports import (
    BoundCommand,
    EffectCall,
    ExecutionClass,
    ExecutionPolicy,
    ExecutionPort,
    ExecutionResult,
    Helpers,
    InRunGroup,
    ReleaseDescriptor,
)
from trestle.workflow.services import AttemptTicket
from trestle.workflow.values import CancelSignal, Confirmation, ConfirmationStatus
from trestle_packs.toolchain.tasks import TaskRunner

from trestle_env.tree import NOTHING


class TaskExecution:
    """`ExecutionPort` over a `TaskRunner` and the plain execution port."""

    def __init__(self, execution: ExecutionPort | None, runner: TaskRunner | None) -> None:
        self._execution = execution
        self._runner = runner

    def bind_evidence(self, sink: Any) -> None:
        """Hand the run's evidence sink to the runner (the toolchain identity is recorded there)."""
        if self._runner is not None:
            self._runner.evidence = sink

    def _plain(self) -> ExecutionPort:
        if self._execution is None:
            raise RuntimeError("no execution port is bound: only a run of nothing is possible")
        return self._execution

    def policy(self, command: BoundCommand) -> ExecutionPolicy:
        if self._runner is not None and command.task != NOTHING:
            return self._runner.policy(command)
        return self._plain().policy(command)

    def release_descriptor(self, call: EffectCall) -> ReleaseDescriptor:
        command = call.arguments.get("command")
        if isinstance(command, BoundCommand) and command.task != NOTHING:
            # helpers a task may leave behind are disclosed by its declared policy (B3-C3)
            return InRunGroup(self.policy(command).helpers is Helpers.DISCLOSED)
        return InRunGroup(False)  # a run of nothing starts no process

    def run(
        self, command: BoundCommand, ticket: AttemptTicket, cancel: CancelSignal, until: datetime
    ) -> tuple[Confirmation, ExecutionResult | None]:
        if command.task == NOTHING:
            passed = ExecutionResult(0, ExecutionClass.PASSED, None, (), None, "")
            return Confirmation(ConfirmationStatus.APPLIED, None, None), passed
        project, _, task = command.task.partition("/")
        if self._runner is None:
            raise RuntimeError("no toolchain is bound: set TRESTLE_MISE_PATH and the projects dir")
        self._runner.cancel = cancel
        return self._runner.run(project, task, ticket, until=until)
