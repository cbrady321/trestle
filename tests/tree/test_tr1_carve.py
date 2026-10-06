"""L.TR-1.3: a worst-case carve misfit is refused before a run id.

B2-C2 (4) and (5) over the whole compiled tree: a child's budget must fit what its parent grants
after the reserve, a shared node is carved at the minimum over its parents, a `ChoiceNode` is
checked at its worst alternative, and the root's release slice and the finalization margin must
fit. A misfit is the V-11 code `BUDGET_DOES_NOT_FIT` (one spelling at admission and in-run)
naming the node, with no run dir and no process. The bounds are the clock's (MC-09), never
restated here."""

from __future__ import annotations

import pytest

from tests.tree.test_tr1_admission import (
    edited,
    publish,
    refused,
    run_dirs,
    source,
)
from trestle.common import clock, codes
from trestle.common.plan.compiler import AdmittedPlan
from trestle.common.types import AdmitRequest
from trestle.server.admission import plan_for_admission
from trestle.server.main import Kernel

DEADLINE_S = 300.0
_RIGHT = '"right": group("right", (SHARED,), budget=100, concurrency=1),'
_EFFECT_IMPORT = "    WorkflowEntry,\n)\n"


def carved(kernel: Kernel, plugin: str) -> AdmittedPlan | object:
    snap = kernel.registry.get(plugin)
    assert snap is not None
    return plan_for_admission(snap, AdmitRequest(plugin=plugin, args={}), DEADLINE_S)


def shared_with_right_budget(seconds: int) -> str:
    return edited("shared_diamond", _RIGHT, _RIGHT.replace("budget=100", f"budget={seconds}"))


def with_release_effect(text: str, release_timeout_s: int) -> str:
    """`text` with one CREATE + RUN effect on its leaves (one release target each)."""
    assert text.count(_EFFECT_IMPORT) == 1
    return text.replace(
        _EFFECT_IMPORT,
        "    EffectDeclaration,\n    EffectFacetClass,\n    Lifetime,\n    WorkflowEntry,\n)\n\n"
        "EFFECTS = (\n    EffectDeclaration(\n        effect='made',\n"
        "        facet=EffectFacetClass.CREATE,\n        verb='',\n        lifetime=Lifetime.RUN,\n"
        "        host_sections=frozenset(),\n"
        f"        release_timeout=timedelta(seconds={release_timeout_s}),\n"
        "    ),\n)\n",
    ).replace("effects=(),", "effects=EFFECTS,")


@pytest.mark.proves("WR-DEADLINE-3", "WR-DEADLINE-3:tree-admission", "A", "tree", "LOGIC", "CI")
@pytest.mark.proves("WR-UNIT-3", "WR-UNIT-3:misfit-refused", "A", "tree", "LOGIC+PROC", "BOTH")
def test_misfit_refused_before_run_id(tree_kernel: Kernel) -> None:
    """`misfit`: the root grants 20 s and its child declares 60 s; the tree publishes and
    admission refuses it naming the child."""
    name = publish(tree_kernel, source("misfit"))
    outcome = refused(tree_kernel, name)
    assert outcome.code == codes.BUDGET_DOES_NOT_FIT == "admission.budget_does_not_fit"
    assert "slow" in outcome.message, outcome.message
    assert run_dirs(tree_kernel) == []


@pytest.mark.proves("WR-UNIT-3", "WR-UNIT-3:misfit-refused", "A", "tree", "LOGIC+PROC", "BOTH")
def test_shared_node_carved_at_min_of_parents(tree_kernel: Kernel) -> None:
    """`shared` (30 s) is one node under `left` (100 s) and `right`: it is carved at the minimum
    of what its parents grant, and refused when either parent grants too little."""
    reserve = clock.FINALIZATION_RESERVE_S
    fits = publish(tree_kernel, shared_with_right_budget(60))
    plan = carved(tree_kernel, fits)
    assert isinstance(plan, AdmittedPlan), plan
    # left grants 100 - reserve, right grants 60 - reserve: the shared slice is the smaller
    assert plan.slices["left/shared"] == min(100 - reserve, 60 - reserve)
    misfit = publish(tree_kernel, shared_with_right_budget(35).replace("shared_diamond", "tight"))
    outcome = refused(tree_kernel, misfit)
    assert outcome.code == codes.BUDGET_DOES_NOT_FIT
    assert "shared" in outcome.message, outcome.message


@pytest.mark.proves("WR-UNIT-3", "WR-UNIT-3:misfit-refused", "A", "tree", "LOGIC+PROC", "BOTH")
def test_choice_worst_case_misfit_refused(tree_kernel: Kernel) -> None:
    """Only one alternative runs, but the carve is the worst case: a `ChoiceNode` whose best
    alternative fits and whose other does not is refused, naming the alternative."""
    fixed = publish(tree_kernel, source("choice_fake"))
    plan = carved(tree_kernel, fixed)
    assert isinstance(plan, AdmittedPlan), plan  # both alternatives fit as authored
    heavy = edited(
        "choice_fake",
        '"fake_b": leaf("fake_b", post="fake_ready"),',
        '"fake_b": leaf("fake_b", post="fake_ready", budget=200),',
    ).replace("choice_fake", "choice_heavy")
    name = publish(tree_kernel, heavy)
    outcome = refused(tree_kernel, name)
    assert outcome.code == codes.BUDGET_DOES_NOT_FIT
    assert "fake_b" in outcome.message, outcome.message


@pytest.mark.proves("WR-UNIT-3", "WR-UNIT-3:misfit-refused", "A", "tree", "LOGIC+PROC", "BOTH")
def test_release_slice_vs_margin_refused(
    tree_kernel: Kernel, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A tree with a release walk: the release slice may not push the root past the admitted
    deadline (B2-C2 (4)) and the finalization the host needs may not exceed the margin (5)."""
    walk = publish(tree_kernel, with_release_effect(source("shared_diamond"), 2))
    assert isinstance(carved(tree_kernel, walk), AdmittedPlan)  # 15 + 10 = 25 <= 35: fits
    # (4) the release slice raised past (admitted deadline - root budget of 240 s)
    monkeypatch.setattr(clock, "release_slice", DEADLINE_S - 240.0 + 1.0)
    outcome = refused(tree_kernel, walk)
    assert outcome.code == codes.BUDGET_DOES_NOT_FIT
    assert "<root>" in outcome.message and "release slice" in outcome.message
    monkeypatch.undo()
    # (5) sweep = ceil(1 / 4) * 5 * 5 = 25; grace + kill + 25 = 40 > 35 (the published margin)
    slow = publish(
        tree_kernel,
        with_release_effect(source("shared_diamond"), 5).replace("shared_diamond", "slow_walk"),
    )
    outcome = refused(tree_kernel, slow)
    assert outcome.code == codes.BUDGET_DOES_NOT_FIT
    assert "finalization" in outcome.message, outcome.message


def test_call_deadline_is_the_carve_and_release_slice_deadline(tree_kernel: Kernel) -> None:
    """v0.4 Feature 0: a call's own `deadline_s` replaces the declared one in the carve, so a
    deadline too short to hold the root budget and the release slice is refused
    `budget_does_not_fit` before a run id, and one that fits is admitted."""
    walk = publish(tree_kernel, with_release_effect(source("shared_diamond"), 2))
    need = 240.0 + clock.release_slice  # the root's budget and the release slice (as above)
    short = tree_kernel.control.admission.admit(
        AdmitRequest(plugin=walk, args={}, deadline_s=need - 1.0)
    )
    assert short.tag == "refused" and short.outcome.code == codes.BUDGET_DOES_NOT_FIT
    assert "release slice" in short.outcome.message, short.outcome.message
    assert not list((tree_kernel.home / "runs").glob("*/*"))
    fits = tree_kernel.control.admission.admit(AdmitRequest(plugin=walk, args={}, deadline_s=need))
    assert fits.tag == "admitted", fits
