"""The answer class of a finished run (MC-17, DM-03): exactly one of six, closed.

`OutcomeClass` is Boundary 4's typed table (interface-terminal-answer.md, `OutcomeClass`), which is
the requirements' outcome decision table (requirements-workflow-runtime.md:245-254). Existing run
states keep their meaning underneath it (WR-COMPAT-8): `RunView.outcome` sits beside `state`.
`repaired` is a disposition beside `passed`, never a class.

For a plan-less plugin (every plugin the core spine runs) the class follows B4-T4: the terminal
kind from the stop cause, `recovered` and the error code. `classify` transcribes that table and
restates nothing else; the code for an execution error is the run's `error_record` code (B2-C10's
terminal-kind -> code map is what the conductor and recovery already wrote there), and
`_KIND_CODE` below is only the fallback for a run whose row is missing or not a code of the
execution vocabulary (MC-CORE-04).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from trestle.common import codes


class OutcomeClass(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    EXECUTION_ERROR = "execution_error"


OUTCOME_CLASSES: frozenset[str] = frozenset(member.value for member in OutcomeClass)

# B4-T4, one row per ledger terminal kind. `failed` is "the plugin raised"; `worker_exit` and
# `crashed` end the plugin process without a normal return and no stop row explains it;
# `interrupted` is a restart (B2-C11, only recovery writes it).
_KIND_CLASS: dict[str, OutcomeClass] = {
    "succeeded": OutcomeClass.PASSED,
    "cancelled": OutcomeClass.CANCELLED,
    "timed_out": OutcomeClass.TIMED_OUT,
    "failed": OutcomeClass.EXECUTION_ERROR,
    "worker_exit": OutcomeClass.EXECUTION_ERROR,
    "crashed": OutcomeClass.EXECUTION_ERROR,
    "interrupted": OutcomeClass.EXECUTION_ERROR,
}

# The classes whose `code` is None (B4-T4: a stop the caller asked for or the deadline).
_NO_CODE = frozenset({OutcomeClass.PASSED, OutcomeClass.CANCELLED, OutcomeClass.TIMED_OUT})

_KIND_CODE: dict[str, str] = {
    "failed": codes.EXECUTION_PLUGIN_RAISED,
    "worker_exit": codes.EXECUTION_WORKER_EXIT,
    "crashed": codes.EXECUTION_WORKER_EXIT,
    "interrupted": codes.EXECUTION_INTERRUPTED,
}


@dataclass(frozen=True)
class Outcome:
    """The class, its code (None for passed, cancelled and timed_out), and whether recovery wrote
    the terminal row."""

    outcome_class: OutcomeClass
    code: str | None
    recovered: bool

    def to_dict(self, snapshot_id: str | None) -> dict[str, Any]:
        """The wire shape `RunView.outcome`: identity names the snapshot the answer is about."""
        return {
            "class": self.outcome_class.value,
            "code": self.code,
            "identity": {"snapshot_id": snapshot_id},
            "recovered": self.recovered,
        }


def classify(
    terminal_kind: str, error_record: dict[str, Any] | None, recovered: bool = False
) -> Outcome:
    """The one class of a run that reached `terminal_kind` (B4-T4).

    `error_record` is the run's ledger row (MC-15) or None; a restart (`recovered`) is always the
    restart code, whatever an earlier row said (B4-C2 rule (2))."""
    try:
        outcome_class = _KIND_CLASS[terminal_kind]
    except KeyError:
        raise ValueError(f"not a terminal kind: {terminal_kind!r}") from None
    if outcome_class in _NO_CODE:
        return Outcome(outcome_class, None, recovered)
    if recovered or terminal_kind == "interrupted":
        return Outcome(outcome_class, codes.EXECUTION_INTERRUPTED, True)
    recorded = error_record.get("code") if error_record else None
    code = recorded if recorded in codes.EXECUTION_CODES else _KIND_CODE[terminal_kind]
    return Outcome(outcome_class, code, False)
