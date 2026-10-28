"""SA-09 in single (L.SV-5.13): the one-vertex fixtures represent multi-node reality well enough
until the tree band (retires at TR-6).

The claim is "the fixtures the spine gate and the termination sweep run cover every LEAF row of
B1-C10's table that can occur at one vertex". Three drift checks, each failing when the input set
falls behind the table, the join or the published fixtures:

* every LEAF (completion x repeat) flag combination, and so every `decide` row of those flags the
  join can produce at one vertex, is swept by an input of the real-loop termination suite
  (`tests/proof/spine/test_termination.py::INPUTS`), and the sweep's rows are the model's;
* every published leaf fixture's declared flags are among the swept inputs (a new fixture with a
  shape the sweep never runs is reported), and the spine fixture is itself swept;
* the rows `decide` defines but the one-vertex join cannot reach are exactly the pinned few (a
  RECORDED leaf's observation carries preconditions only, so it has no STALE or INCOMPATIBLE), so a
  change to the table or the join moves this list and is seen here.
"""

from __future__ import annotations

import pytest

from tests.proof.spine import test_termination as suite
from trestle.workflow.decide import NeverProduced, decide
from trestle.workflow.declarations import (
    CompletionSource,
    Compose,
    LeafDeclaration,
    LoopFlags,
    Repeat,
)
from trestle.workflow.values import Condition, Goal

LEAF_FLAGS = [
    LoopFlags(Compose.LEAF, completion, repeat)
    for completion in CompletionSource
    for repeat in Repeat
]
# rows `decide` defines for a RECORDED leaf that its one-vertex join cannot produce
RECORDED_UNREACHABLE = frozenset({"incompatible", "stale"})


def _defined(flags: LoopFlags) -> frozenset[str]:
    rows: set[str] = set()
    for condition in Condition:
        try:
            decide(flags, condition, Goal.CONVERGE)
        except NeverProduced:
            continue
        rows.add(condition.value)
    return frozenset(rows)


def _fixture_leaf_flags() -> dict[str, LoopFlags]:
    found: dict[str, LoopFlags] = {}
    for path in sorted(suite.FIXTURES.glob("*.py")):
        if path.name.startswith("_"):
            continue
        source = path.read_text(encoding="utf-8")
        if "DECLARATION" not in source or "LeafDeclaration" not in source:
            continue
        decl = suite.fixture_declaration(path.name)
        assert isinstance(decl, LeafDeclaration)
        found[path.stem] = decl.flags
    return found


@pytest.mark.parametrize("sa", ["SA-09"])
def test_fixtures_cover_every_reachable_leaf_row(sa: str) -> None:
    swept = {inp.flags for inp in suite.INPUTS.values()}
    # every LEAF flag combination is swept by an input of the real-loop sweep
    assert set(LEAF_FLAGS) <= swept, [f for f in LEAF_FLAGS if f not in swept]
    for flags in LEAF_FLAGS:
        reachable = suite.reachable_conditions(flags)
        defined = _defined(flags)
        # the join produces nothing `decide` has no row for, and the rest is the pinned few
        assert reachable <= defined, (flags, reachable - defined)
        unreachable = defined - reachable
        want = RECORDED_UNREACHABLE if flags.completion is CompletionSource.RECORDED else set()
        assert unreachable == want, (flags, unreachable)


@pytest.mark.parametrize("sa", ["SA-09"])
def test_every_published_leaf_fixture_is_swept(sa: str) -> None:
    fixtures = _fixture_leaf_flags()
    assert "spine_leaf" in fixtures, "the spine fixture is a leaf fixture"
    swept = {inp.flags for inp in suite.INPUTS.values()}
    for name, flags in fixtures.items():
        assert flags in swept, f"fixture {name} declares flags no termination input sweeps"
        assert name in suite.INPUTS, f"fixture {name} is not itself swept by test_termination"
