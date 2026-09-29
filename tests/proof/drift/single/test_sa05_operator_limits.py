"""SA-05 in the single phase (L.SV-3.4; extended by L.SV-3.7): the release slice, the finalization
reserve and the margin are published once in `trestle/common/clock.py`, read by the proof court
through `tests.proof.tolerances` (no edit there, DM-60), and the margin covers B2-C2 (5) for the
A-1 fixture roots at the published defaults."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.proof import tolerances
from tests.proof.foundations import trees
from trestle.common import clock
from trestle.common.plan import carving, compiler
from trestle.common.plan.declared import ROOT_PATH, DeclaredTree

ROOT = Path(__file__).resolve().parents[4]
CLOCK = ROOT / "trestle" / "common" / "clock.py"


def _fixture_root(*release_timeouts: float) -> compiler.AdmittedPlan:
    """A one-leaf root with one CREATE+RUN effect (one sweep target) per release timeout."""
    effects = [trees.create_run_effect(f"e{i}", t) for i, t in enumerate(release_timeouts)]
    node = trees.leaf_node("fixture", budget=None, effects=effects)
    plan = compiler.compile(DeclaredTree.build("fixture", {ROOT_PATH: node}), {})
    assert isinstance(plan, compiler.AdmittedPlan)
    return plan


def _limits() -> carving.MarginLimits:
    return carving.MarginLimits(
        grace=tolerances.grace(),
        kill=tolerances.kill(),
        sweep_parallelism=int(tolerances.sweep_parallelism()),
    )


def _fixture_roots() -> dict[str, compiler.AdmittedPlan]:
    # release timeouts derived from the published margin: the room the margin leaves for the sweep,
    # split over the five steps of one target; no timing literal here (SA-05)
    room = tolerances.finalization_margin() - tolerances.grace() - tolerances.kill()
    one_target = room / carving.SWEEP_STEPS
    return {
        "plain plugin (implicit depth-1 plan)": compiler.implicit_depth1_plan("plain"),
        "leaf, no release walk": _fixture_root(),
        "leaf, one target at half the room": _fixture_root(one_target / 2),
        "leaf, one target using all the room": _fixture_root(one_target),
        "leaf, targets up to sweep_parallelism in one rank": _fixture_root(
            *([one_target] * int(tolerances.sweep_parallelism()))
        ),
    }


@pytest.mark.parametrize("sa", ["SA-05"])
def test_margin_covers_b2c2_5_for_fixture_roots(sa: str) -> None:
    for label, plan in _fixture_roots().items():
        needed = carving.margin_needed(plan, _limits())
        assert needed <= tolerances.finalization_margin(), (label, needed)
    # and the check has teeth: one more second on one target no longer fits
    room = tolerances.finalization_margin() - tolerances.grace() - tolerances.kill()
    over = _fixture_root(room / carving.SWEEP_STEPS + 1)
    assert carving.margin_needed(over, _limits()) > tolerances.finalization_margin()
    # a plain plugin is covered by the stop bound alone (B2-C1: release slice 0)
    plain = carving.margin_needed(compiler.implicit_depth1_plan("plain"), _limits())
    assert plain == tolerances.grace() + tolerances.kill() < tolerances.finalization_margin()


def _defs(name: str) -> list[str]:
    found = []
    for path in sorted((ROOT / "trestle").rglob("*.py")):
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            targets: list[ast.expr] = []
            if isinstance(node, ast.Assign):
                targets = list(node.targets)
            elif isinstance(node, ast.AnnAssign):
                targets = [node.target]
            if any(isinstance(t, ast.Name) and t.id == name for t in targets):
                found.append(str(path.relative_to(ROOT)))
    return found


@pytest.mark.parametrize("sa", ["SA-05"])
def test_stop_bound_is_release_slice_grace_kill(sa: str) -> None:
    # B2-C10: stop_bound = release_slice + grace + kill, defined once, in clock.py
    for name in ("stop_bound", "release_slice", "FINALIZATION_RESERVE_S", "sweep_parallelism"):
        assert _defs(name) == ["trestle/common/clock.py"], name
    assert clock.stop_bound == clock.release_slice + clock.grace + clock.kill
    assert tolerances.stop_bound() == tolerances.release_slice() + tolerances.grace() + (
        tolerances.kill()
    )
    tree = ast.parse(CLOCK.read_text(encoding="utf-8"))
    stop = next(
        n.value
        for n in tree.body
        if isinstance(n, ast.AnnAssign)
        and isinstance(n.target, ast.Name)
        and n.target.id == "stop_bound"
        and n.value is not None
    )
    names = {n.id for n in ast.walk(stop) if isinstance(n, ast.Name)}
    assert names == {"release_slice", "grace", "kill"}  # composed from the three, no literal
    # the tolerances module reads every one of them with no edit (DM-60)
    assert tolerances.finalization_reserve_s() == clock.FINALIZATION_RESERVE_S
