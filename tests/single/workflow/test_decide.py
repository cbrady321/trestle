"""L.SV-5.3: the work-unit contract types, the loop-side record view and the pure `decide`
over B1-C10's normative table (MC-24)."""

from __future__ import annotations

import ast
import dataclasses
import inspect
import itertools
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from trestle.workflow import decide as decide_module
from trestle.workflow import units
from trestle.workflow.decide import Command, NeverProduced, decide
from trestle.workflow.declarations import CompletionSource, Compose, LoopFlags, Repeat
from trestle.workflow.values import Condition, Goal, Resend, StepKind

ROOT = Path(__file__).resolve().parents[3]
PACKAGE = ROOT / "trestle" / "workflow"

C, G = Condition, Goal
OBS, REC = CompletionSource.OBSERVED, CompletionSource.RECORDED
SAFE, ONCE = Repeat.SAFE, Repeat.ONCE
CMD = Command

_TERMINAL = (C.SATISFIED, C.BLOCKED, C.FAILED, C.INCOMPATIBLE)


def _table_row(
    goal: Goal,
    compose: Compose,
    completion: CompletionSource,
    repeat: Repeat,
    condition: Condition,
) -> tuple[Command, ...] | None:
    """B1-C10's decision table as the interface writes it, row by row (None: never produced).

    Independent of `decide`: every row of `interface-work-unit.md` "The decision table" is one
    branch here, in the table's order."""
    if goal is G.CONVERGE and compose is Compose.ALL:
        return (CMD.WALK,)
    if goal is G.CONVERGE and compose is Compose.CHOICE:
        return (CMD.SELECT, CMD.WALK)
    if goal is G.CONVERGE and compose is Compose.LEAF:
        if condition in _TERMINAL:
            return (CMD.STOP,)
        if completion is OBS and condition is C.UNSATISFIED:
            return (CMD.ADVANCE, CMD.POLL)
        if completion is OBS and condition is C.CONVERGING:
            return (CMD.POLL,)
        if completion is REC and condition is C.UNSATISFIED:
            return (CMD.ADVANCE, CMD.REJOIN)
        if completion is OBS and repeat is ONCE and condition is C.IN_DOUBT:
            return (CMD.POLL,)
        if completion is REC and repeat is ONCE and condition is C.IN_DOUBT:
            return (CMD.STOP,)
        if condition is C.STALE:
            return (CMD.POLL,)
        return None
    if goal is G.RELEASE and compose in (Compose.ALL, Compose.CHOICE):
        return (CMD.WALK,)
    assert goal is G.RELEASE and compose is Compose.LEAF
    return (CMD.RELEASE_HANDLES,)


def _never_produced(
    goal: Goal,
    compose: Compose,
    completion: CompletionSource,
    repeat: Repeat,
    condition: Condition,
) -> bool:
    """The combinations B1-C10 says the join never produces (a LEAF under CONVERGE)."""
    if goal is not G.CONVERGE or compose is not Compose.LEAF:
        return False
    return (completion is REC and condition is C.CONVERGING) or (
        repeat is SAFE and condition is C.IN_DOUBT
    )


ALL_INPUTS = list(itertools.product(G, Compose, CompletionSource, Repeat, Condition))


def test_decide_matches_normative_table_exhaustively() -> None:
    assert len(ALL_INPUTS) == 2 * 3 * 2 * 2 * 8
    produced = 0
    for goal, compose, completion, repeat, condition in ALL_INPUTS:
        flags = LoopFlags(compose, completion, repeat)
        expected = _table_row(goal, compose, completion, repeat, condition)
        label = (goal.value, compose.value, completion.value, repeat.value, condition.value)
        if _never_produced(goal, compose, completion, repeat, condition):
            assert expected is None, label
            with pytest.raises(NeverProduced):
                decide(flags, condition, goal)
            continue
        assert expected is not None, f"table has no row for {label}"
        assert decide(flags, condition, goal) == expected, label
        produced += 1
    # never produced: RECORDED x CONVERGING (2 repeats) and SAFE x IN_DOUBT (2 completions)
    assert produced == len(ALL_INPUTS) - 4


def test_recorded_unsatisfied_returns_advance_rejoin() -> None:
    for repeat in Repeat:
        flags = LoopFlags(Compose.LEAF, REC, repeat)
        assert decide(flags, C.UNSATISFIED, G.CONVERGE) == (CMD.ADVANCE, CMD.REJOIN)
    # an OBSERVED leaf polls after advancing; only a RECORDED leaf rejoins from the record
    observed = LoopFlags(Compose.LEAF, OBS, SAFE)
    assert decide(observed, C.UNSATISFIED, G.CONVERGE) == (CMD.ADVANCE, CMD.POLL)
    # RECORDED + ONCE + IN_DOUBT stops: there is nothing to poll (J-8)
    recorded_once = LoopFlags(Compose.LEAF, REC, ONCE)
    assert decide(recorded_once, C.IN_DOUBT, G.CONVERGE) == (CMD.STOP,)


def test_command_set_is_the_seven_hld_commands() -> None:
    assert {c.name: c.value for c in Command} == {
        "WALK": "walk",
        "SELECT": "select",
        "ADVANCE": "advance",
        "POLL": "poll",
        "REJOIN": "rejoin",
        "RELEASE_HANDLES": "release",
        "STOP": "stop",
    }
    assert [c.name for c in Command] == [
        "WALK",
        "SELECT",
        "ADVANCE",
        "POLL",
        "REJOIN",
        "RELEASE_HANDLES",
        "STOP",
    ]
    # every command decide can return is a member (no stray string)
    for goal, compose, completion, repeat, condition in ALL_INPUTS:
        if _never_produced(goal, compose, completion, repeat, condition):
            continue
        out = decide(LoopFlags(compose, completion, repeat), condition, goal)
        assert isinstance(out, tuple) and out and all(isinstance(c, Command) for c in out)
    signature = inspect.signature(decide)
    assert list(signature.parameters) == ["flags", "condition", "goal"]


_ALLOWED_STDLIB_FOR_DECIDE = {"__future__", "enum"}


def _imports(path: Path) -> list[str]:
    names: list[str] = []
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


def test_decide_pure_no_io() -> None:
    path = Path(decide_module.__file__)
    for name in _imports(path):
        top = name.split(".")[0]
        assert top in _ALLOWED_STDLIB_FOR_DECIDE or name.startswith("trestle.workflow"), name
    tree = ast.parse(path.read_text())
    forbidden_calls = {"open", "print", "input", "exec", "eval", "__import__"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in forbidden_calls, node.func.id
        assert not isinstance(node, ast.Global | ast.Nonlocal)
    # same inputs, same answer, and the inputs are not mutated (frozen values)
    flags = LoopFlags(Compose.LEAF, OBS, SAFE)
    first = decide(flags, C.UNSATISFIED, G.CONVERGE)
    assert all(decide(flags, C.UNSATISFIED, G.CONVERGE) == first for _ in range(3))


# V-4's TicketEntry and StepEntry, field for field, in V-4's order (00-shared-vocabulary.md V-4).
V4_TICKET_ENTRY = (
    "lineage",
    "effect",
    "facet",
    "attempt",
    "repeat",
    "lifetime",
    "release",
    "remedy",
    "issued_at",
    "confirmation",
    "handle",
    "result",
    "released_at",
    "release_outcome",
)
V4_STEP_ENTRY = ("lineage", "at", "kind", "code", "human_action", "resend", "handle")


def _step(at: int, code: str = "x") -> units.StepView:
    from trestle.workflow.values import Lineage, NodePath

    return units.StepView(
        lineage=Lineage("r1", NodePath(())),
        at=datetime(2026, 1, 1, tzinfo=UTC).replace(second=at),
        kind=StepKind.BLOCKED,
        code=code,
        human_action="do it",
        resend=Resend.UNKNOWN,
        handle=None,
    )


def test_record_view_fields_transcribe_v4() -> None:
    assert tuple(f.name for f in dataclasses.fields(units.TicketView)) == V4_TICKET_ENTRY
    assert tuple(f.name for f in dataclasses.fields(units.StepView)) == V4_STEP_ENTRY
    assert tuple(f.name for f in dataclasses.fields(units.NodeRecordView)) == ("tickets", "steps")
    for cls in (units.TicketView, units.StepView, units.NodeRecordView):
        params = cls.__dataclass_params__  # type: ignore[attr-defined]
        assert params.frozen and hasattr(cls, "__slots__")

    durable = units.NodeRecordView(steps=(_step(1, "a"), _step(2, "b")))
    held = (_step(3, "c"), _step(4, "d"))
    merged = durable.with_held(held)
    assert [s.code for s in merged.steps] == ["a", "b", "c", "d"]  # appended after, in order
    assert merged.tickets == durable.tickets
    assert [s.code for s in durable.steps] == ["a", "b"]  # the durable part is unchanged
    assert durable.with_held(()) == durable

    # units.py imports only stdlib and this package's own modules (C.5 step 4)
    stdlib = set(sys.stdlib_module_names)
    for name in _imports(Path(units.__file__)):
        assert name.split(".")[0] in stdlib or name.startswith("trestle.workflow"), name
