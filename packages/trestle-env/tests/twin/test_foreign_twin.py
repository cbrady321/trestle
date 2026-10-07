"""L.RB-3.2 twins: a foreign occupant and an unhealthy found resource end blocked, on the fake
binding (STUB · CI). The CI twins of `host/test_foreign.py`, same node names.

The tree's own units run through the loop on `twin.twin_engine.SupportEngine` holding a found
container named as the logical system: one whose identity does not check out (foreign), and one
whose identity and configuration do but whose readiness never passes (unhealthy). Each ends BLOCKED
on `postgres` with its stable code and a human action (B4-T2 row 10, V-11.1), and no effect
ever acts on the found container (never signalled, adopted or repaired). Registers only
`@stub-twin` labels.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.tree import treekit as tk

from trestle_env import tree
from twin.twin_engine import SupportEngine, rig_over

FOUND = tree.POSTGRES_SERVICE


def _blocked_untouched(tmp_path: Path, engine: SupportEngine, code: str) -> None:
    engine.plant_found(FOUND)
    rig = rig_over(tmp_path, engine)
    rig.run()
    answer = tk.answer_of(rig)
    assert answer.outcome == "blocked"
    assert answer.primary.path == (tree.POSTGRES_SERVICE,)
    assert answer.primary.code == code
    assert answer.primary.human_action  # V-11.1: what a person does, then re-send
    assert FOUND in engine.inventory()["containers"]
    assert engine._containers[FOUND].state == "running"  # noqa: SLF001
    assert [e for e in engine.effects if e[1] == FOUND] == []  # never signalled or repaired
    assert engine.inventory()["containers"] == frozenset({FOUND})  # what the run made is gone


@pytest.mark.stub_proven("WR-OWN-7:b-foreign-blocked-not-killed@stub-twin")
@pytest.mark.parametrize("occupant", ["container"])
def test_foreign_occupant_blocked_never_signalled(occupant: str, tmp_path: Path) -> None:
    _blocked_untouched(tmp_path, SupportEngine(identity_ok=False), "execution.found_incompatible")


@pytest.mark.stub_proven("WR-OWN-10:b-unhealthy-found-blocked@stub-twin")
def test_unhealthy_found_blocked_not_repaired(tmp_path: Path) -> None:
    _blocked_untouched(tmp_path, SupportEngine(password_ok=False), "execution.found_unhealthy")
