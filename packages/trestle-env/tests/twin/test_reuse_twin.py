"""A pre-started Postgres is reused and left untouched on every path, on the fake binding
(L.RB-3.1; MC-B-03): the CI twin of `tests/host/test_reuse.py`.

The fake engine holds a found container named as the logical system (`postgres`), whose identity,
configuration and readiness the reuse proof reads. The tree's own units run through the loop:
the supporting service is created, the found Postgres is reported reused, and on the failed and
the cancelled path (the supporting service never ready; a stop raised during its wait) the found
container (reused by its own sibling node meanwhile) is never created over, stopped, restarted,
recreated or started."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.tree import treekit as tk
from trestle.common.plan.vocabulary import ResourceDisposition

from trestle_env import tree
from twin.twin_engine import SupportEngine, rig_over

FOUND = tree.POSTGRES_SERVICE  # the exact name of the logical system: what a run finds


def dispositions(answer: object) -> dict[str, str | None]:
    nodes = [answer.primary, *answer.listed]  # type: ignore[attr-defined]
    return {"/".join(n.path): n.disposition for n in nodes if n.path}


def untouched(engine: SupportEngine) -> None:
    """The found container is still there, running, and no effect acted on it."""
    assert FOUND in engine.inventory()["containers"]
    assert engine._containers[FOUND].state == "running"  # noqa: SLF001
    assert [e for e in engine.effects if e[1] == FOUND] == []


@pytest.mark.stub_proven("WR-OWN-1:b-dispositions-reused-started@stub-twin")
@pytest.mark.stub_proven("WR-OWN-2:b-found-untouched-all-paths@stub-twin")
@pytest.mark.stub_proven("WR-OWN-10:b-cleanup-never-found@stub-twin")
@pytest.mark.parametrize("path", ["passed", "failed", "cancelled"])
def test_prestarted_postgres_reused_untouched(tmp_path: Path, path: str) -> None:
    engine = SupportEngine(serves_declared=path == "passed")
    engine.plant_found(FOUND)
    rig = rig_over(tmp_path, engine)
    if path == "cancelled":
        rig.rig.cancel.stop_on_wait = 2  # a stop during the supporting node's readiness wait
    rig.run()
    if path == "cancelled":  # the rig has no supervisor to write the stop row: read the lane
        assert rig.rig.cancel.requested
        ends = rig.ends()
        assert ends[tree.HTTP_SUPPORT_SERVICE]["cut"] == "stopped"  # cut mid-wait by the stop
        # Postgres depends on the supporting service (the catalog's depends_on): the stop came
        # before it could start, and the found Postgres is untouched all the same
        assert ends[tree.POSTGRES_SERVICE]["cut"] == "not_started"
        untouched(engine)
        assert engine.inventory()["containers"] == frozenset({FOUND})  # the run's own released
        return
    answer = tk.answer_of(rig)
    if path == "passed":
        assert answer.outcome == "passed"
        shown = dispositions(answer)
        assert shown[tree.POSTGRES_SERVICE] == ResourceDisposition.REUSED
        assert shown[tree.HTTP_SUPPORT_SERVICE] == ResourceDisposition.STARTED
        # reused is reported, and nothing was created for it: no ticket for the found node
        rows = rig.rows()
        assert not any(
            r.get("path") == tree.POSTGRES_SERVICE and r["class"] == "issue" for r in rows
        )
        # the reuse proof was read from the found resource
        assert {tree.POSTGRES_IDENTITY, tree.POSTGRES_CONFIGURATION, tree.POSTGRES_READY} <= set(
            engine.asked
        )
    else:
        assert answer.outcome == "blocked"  # the supporting service never became ready
    untouched(engine)
    # what the run created (the supporting container) is released; only the found one remains
    assert engine.inventory()["containers"] == frozenset({FOUND})


def test_a_found_postgres_without_proven_identity_is_never_reused(tmp_path: Path) -> None:
    """The proof is read, not assumed: a found container whose identity does not check out is
    incompatible (WR-OWN-7), never reused, stopped or adopted (L.RB-3.2 owns the full case)."""
    engine = SupportEngine(identity_ok=False)
    engine.plant_found(FOUND)
    rig = rig_over(tmp_path, engine)
    rig.run()
    answer = tk.answer_of(rig)
    assert answer.outcome == "blocked"
    assert answer.primary.path == (tree.POSTGRES_SERVICE,)
    assert answer.primary.code == "execution.found_incompatible"
    untouched(engine)
