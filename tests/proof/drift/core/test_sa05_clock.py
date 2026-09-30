"""SA-05 in core (L.CS-1.2): every bound `trestle/common/clock.py` publishes has one AST
definition, is read by the proof court only through `tests.proof.tolerances` under its MC-09
name, and no core test hard-codes a timing literal."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.proof import tolerances

ROOT = Path(__file__).resolve().parents[4]
CLOCK = ROOT / "trestle" / "common" / "clock.py"
TIMING_KEYWORDS = {"timeout", "timeout_s", "wait_ms", "grace_s", "kill_s", "deadline_s"}
TEST_TREES = ("tests/core", "tests/spine")


def _module_level_names(tree: ast.Module) -> list[str]:
    names: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names += [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.append(node.target.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.append(node.name)
    return names


def _published() -> list[str]:
    return [n for n in _module_level_names(ast.parse(CLOCK.read_text())) if not n.startswith("_")]


@pytest.mark.parametrize("sa", ["SA-05"])
def test_every_clock_bound_is_defined_once(sa: str) -> None:
    published = _published()
    assert "APPEND_COST_RATIO" in published
    assert len(published) == len(set(published)), published
    for name in published:
        defined_in = [
            str(path.relative_to(ROOT))
            for path in sorted((ROOT / "trestle").rglob("*.py"))
            if name in _module_level_names(ast.parse(path.read_text()))
        ]
        assert defined_in == ["trestle/common/clock.py"], (name, defined_in)


@pytest.mark.parametrize("sa", ["SA-05"])
def test_every_clock_bound_is_read_through_tolerances(sa: str) -> None:
    from trestle.common import clock

    # the tolerances module resolves the bound from clock.py with no edit (DM-60)
    assert tolerances.append_cost_ratio() == float(clock.APPEND_COST_RATIO)
    # every published bound is readable through tolerances under its own name, with no edit there:
    # `_resolve` reads clock.py first, so a bound tolerances has no accessor for (the supervisor's
    # `poll_interval`, CB-9) is still read from the one definition
    for name in _published():
        assert tolerances._resolve(name) == float(getattr(clock, name)), name  # noqa: SLF001
    # the stop bound is release_slice + grace + kill, as B2-C10 defines it (release slice: SV-3.4)
    assert clock.stop_bound == clock.release_slice + clock.grace + clock.kill
    assert tolerances.stop_bound() == clock.stop_bound


@pytest.mark.parametrize("sa", ["SA-05"])
def test_margin_covers_stop_bound(sa: str) -> None:
    """B2-C2 (5) in its plan-less form (L.CS-4.1): a root with no release target has
    `grace + kill <= finalization_margin`, and the default leaves room for the finalization writes
    after the stop (strictly more than the stop bound, which now includes the published release
    slice, L.SV-3.4). SV-3 extends the check to the release targets (test_sa05_operator_limits).
    A deadline stop of a plugin that ignores SIGTERM therefore still answers inside
    `spec.deadline + finalization_margin`."""
    from trestle.common import clock

    assert clock.stop_bound == clock.release_slice + clock.grace + clock.kill
    assert clock.grace + clock.kill <= clock.finalization_margin
    assert clock.finalization_margin > clock.stop_bound  # room for the finalization writes
    assert tolerances.finalization_margin() == clock.finalization_margin


@pytest.mark.parametrize("sa", ["SA-05"])
def test_no_timing_literal_in_core_tests(sa: str) -> None:
    offenders: list[str] = []
    for base in TEST_TREES:
        for path in sorted((ROOT / base).rglob("*.py")):
            for node in ast.walk(ast.parse(path.read_text())):
                if not isinstance(node, ast.Call):
                    continue
                candidates = [kw.value for kw in node.keywords if kw.arg in TIMING_KEYWORDS]
                func = node.func
                name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
                if name == "sleep":
                    candidates += node.args[:1]
                for value in candidates:
                    if isinstance(value, ast.Constant) and isinstance(value.value, (int, float)):
                        if not isinstance(value.value, bool):
                            offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert not offenders, offenders
