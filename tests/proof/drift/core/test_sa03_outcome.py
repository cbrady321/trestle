"""SA-03 in core (L.CS-4.3): the answer class set is one set. `outcome.OUTCOME_CLASSES` equals the
requirements' outcome decision table and Boundary 4's `OutcomeClass`, each transcribed below with
its citation, and `repaired` is not a class (DM-03)."""

from __future__ import annotations

import pytest

from trestle.common import outcome

# requirements-workflow-runtime.md L245-254, "Outcome decision table": the Class column, in order
REQUIREMENTS_TABLE = ["passed", "failed", "blocked", "cancelled", "timed out", "execution error"]

# interface-terminal-answer.md, `class OutcomeClass(StrEnum)` ("the requirements' closed table,
# requirements-workflow-runtime.md:245-254, typed"): member -> value
B4_OUTCOME_CLASS = {
    "PASSED": "passed",
    "FAILED": "failed",
    "BLOCKED": "blocked",
    "CANCELLED": "cancelled",
    "TIMED_OUT": "timed_out",
    "EXECUTION_ERROR": "execution_error",
}


def _token(class_name: str) -> str:
    return class_name.replace(" ", "_")


@pytest.mark.parametrize("sa", ["SA-03"])
def test_class_set_equals_requirements_table_and_b4(sa: str) -> None:
    table = {_token(name) for name in REQUIREMENTS_TABLE}
    assert len(table) == len(REQUIREMENTS_TABLE) == 6
    assert outcome.OUTCOME_CLASSES == table
    assert outcome.OUTCOME_CLASSES == set(B4_OUTCOME_CLASS.values())
    assert {m.name: m.value for m in outcome.OutcomeClass} == B4_OUTCOME_CLASS
    assert "repaired" not in outcome.OUTCOME_CLASSES  # a disposition beside passed, never a class
