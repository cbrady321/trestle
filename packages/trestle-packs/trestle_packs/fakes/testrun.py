"""Fake test runner (L.RB-5.1; B3-C14, B3-C21, DM-09).

Stdlib only, like its siblings. `FakeTestRun(results, outputs)` is a `FakeCommand` (an
`ExecutionPort`; `TestRun` is `ExecutionPort`, B3-C21) that also keeps the full console output of a
run behind a handle, as `PytestJunitRunner` does: `results` maps a command's `task` to the scripted
`ExecutionResult`, `outputs` maps it to the full console text the run "produced". Its `excerpt` is
the bounded tail of that text, so a result and its handle agree as the real runner's do. It runs
nothing. The same conformance cases (`tests/conformance/testrun_cases.py`) run against it and
against the real runner.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from trestle_packs.fakes.command import (
    Confirmation,
    ConfirmationStatus,
    ExecutionResult,
    FakeCommand,
    Scripted,
)

TEXT_MAX = 512  # V-13 TEXT_MAX / EVENT_MAX under TRESTLE_TEST_LIMITS


@dataclass(frozen=True, slots=True)
class FakeOutputHandle:
    """The full output of one run (the fields of `trestle_packs.testrun.OutputHandle`)."""

    data: bytes

    @property
    def path(self) -> str:
        return f"fake://output/{self.sha256}"

    @property
    def size(self) -> int:
        return len(self.data)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.data).hexdigest()

    def read(self) -> bytes:
        return self.data


class FakeTestRun(FakeCommand):
    """A scripted `ExecutionPort` for test selectors, with the full output behind a handle."""

    def __init__(
        self,
        results: Mapping[str, Scripted],
        outputs: Mapping[str, str] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(results, **kwargs)
        self._outputs = dict(outputs or {})
        self._handles: dict[tuple[Any, ...], FakeOutputHandle] = {}

    def output(self, ticket: Any) -> FakeOutputHandle | None:
        return self._handles.get(self._key(ticket))

    def run(
        self, command: Any, ticket: Any, cancel: Any, until: datetime
    ) -> tuple[Confirmation, ExecutionResult | None]:
        confirmation, result = super().run(command, ticket, cancel, until)
        text = self._outputs.get(command.task)
        if text is None or result is None or confirmation.status is not ConfirmationStatus.APPLIED:
            return confirmation, result
        data = text.encode("utf-8")
        self._handles[self._key(ticket)] = FakeOutputHandle(data)
        return confirmation, replace(result, excerpt=data[-TEXT_MAX:].decode("utf-8", "ignore"))

    @staticmethod
    def _key(ticket: Any) -> tuple[Any, ...]:
        lineage = ticket.lineage
        return (lineage.root_run_id, tuple(lineage.path.segments), ticket.effect, ticket.attempt)
