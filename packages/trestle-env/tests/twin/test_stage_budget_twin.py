"""A never-ready service ends at its stage budget, on the fake binding (L.RB-2.2; MC-B-03).

The CI twin of `tests/host/test_stage_budget.py`: the tree's own units on the fake engine, the
Postgres check refusing its password, a found container this run never made beside them. The
answer is the host's own projection over the run's lane."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest
from tests.single.workflow import loopkit as kit
from tests.tree import treekit as tk
from trestle.common.plan import vocabulary
from twin_engine import REFUSED, SupportEngine, rig_over

from trestle_env import stages, tree


@pytest.mark.stub_proven("WR-ENV-9:stage-k-named-found-untouched@stub-twin")
def test_readiness_ends_at_stage_budget_names_stage_and_service(tmp_path: Path) -> None:
    engine = SupportEngine(password_ok=False)
    engine.plant_found("bystander")  # a found resource: this run never made it, never touches it
    before = engine.inventory()
    rig = rig_over(tmp_path, engine)
    rig.run()
    answer = tk.answer_of(rig)
    # BLOCKED through the EXHAUSTED class (B4-T2 row 8, B4-T3): POSTCONDITION_TIMEOUT, no new code
    assert answer.outcome == "blocked"
    primary = answer.primary
    assert primary.code == vocabulary.POSTCONDITION_TIMEOUT
    assert primary.node_class == vocabulary.NodeClass.EXHAUSTED
    assert primary.human_action and primary.resend is not None  # B4-C5
    # the answer names the readiness stage and the failing service
    assert primary.path == (tree.POSTGRES_UNIT,)
    named = stages.failure_at("/".join(primary.path), primary.code or "")
    assert named == stages.StageFailure(stages.Stage.READINESS, "postgres", primary.code or "")
    assert "backend.postgres" in primary.human_action
    # it ended when its declared wait elapsed (plus the supporting node's own polls and the margin)
    budget = tree.READY_WAIT_S + (REFUSED + 1) * tree.READY_POLL_S + kit.MARGIN_S
    assert rig.rig.clock.now - kit.NOW <= timedelta(seconds=budget)
    assert engine.asked.count(tree.POSTGRES_READY) >= tree.READY_WAIT_S // tree.READY_POLL_S
    # found fingerprints unchanged: the bystander is untouched and only what the run made is gone
    assert engine.inventory() == before
    assert engine.inventory()["containers"] == frozenset({"bystander"})
