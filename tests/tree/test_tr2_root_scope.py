"""L.TR-2.3: a child's join input is read from its own root run's lane only.

The join input is V-3's fresh observation joined with the node's `NodeRecord` (V-4.5), which is
`AttemptLane.node_record(path)` in this root. Two roots share a machine here (two run
directories side by side, each with its own lane through MC-19's `AttemptLane`); the test acts as
the loop (no walk, TR-2 preamble): it writes the plan, issues, confirms and records results, then
joins. The same cases under MC-26 `run_tree`, which need the composite walk, are `L.TR-3.2`'s
(A2c6-1)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tests.single.record import support as sup
from tests.single.workflow import joinkit as jk
from trestle.child.attempt_lane import AttemptLane, AttemptTicket, TicketRefusal
from trestle.child.run_services import node_record_view
from trestle.common import lane_format as lf
from trestle.common.plan import bounds
from trestle.workflow.join import join
from trestle.workflow.units import NodeRecordView
from trestle.workflow.values import Condition, NodeTerms, Observation, Provenance, Verdict

R1, R2 = "run-root-r1", "run-root-r2"
CHILD = ("child",)
SCOPE = ((), CHILD)
NOW = datetime(2026, 9, 30, 12, 0, 0, tzinfo=UTC)

proves_other = pytest.mark.proves(
    "WR-UNIT-2", "WR-UNIT-2:other-root-obs-not-counted", "A", "tree", "LOGIC", "CI"
)
proves_own = pytest.mark.proves(
    "WR-UNIT-2", "WR-UNIT-2:own-root-obs-counted", "A", "tree", "LOGIC", "CI"
)
proves_verify = pytest.mark.proves("WR-VERIFY-7", "WR-VERIFY-7:tree", "A", "tree", "LOGIC", "CI")
proves_scoped = pytest.mark.proves(
    "WR-IDEM-3", "WR-IDEM-3:tree-root-scoped", "A", "tree", "LOGIC", "CI"
)
proves_once = pytest.mark.proves("WR-IDEM-5", "WR-IDEM-5:tree-A", "A", "tree", "LOGIC", "CI")


class _Clock:
    """One model clock for both lanes: every write moves it on, so record order is time order."""

    t = 0.0

    @classmethod
    def now(cls) -> datetime:
        return NOW + timedelta(seconds=cls.t)

    @classmethod
    def tick(cls) -> datetime:
        cls.t += 10.0
        return cls.now()


PASSED = lf.RecordedResult(passed=True, code=None, counts=None)
FAILED = lf.RecordedResult(passed=False, code="unit.tests_failed", counts=None)


def _lane(runs: Path, root: str) -> AttemptLane:
    """One root run's lane: `<runs>/<root>` is the run directory (its name is the root, B2-I3)."""
    lane = AttemptLane(runs / root, bounds.LANE_BASE_ENTRIES, SCOPE, now=_Clock.now)
    lane.record_plan(sup.plan_entry(0).plan)
    return lane


@pytest.fixture
def roots(tmp_path: Path) -> tuple[AttemptLane, AttemptLane]:
    runs = tmp_path / "runs"
    runs.mkdir()
    _Clock.t = 0.0
    return _lane(runs, R1), _lane(runs, R2)


def _issue(
    lane: AttemptLane,
    root: str,
    *,
    effect: str = "run_tests",
    repeat: lf.Repeat = lf.Repeat.SAFE,
    max_attempts: int = 5,
) -> AttemptTicket | TicketRefusal:
    return lane.issue(
        sup.lineage(*CHILD, root=root),
        effect,
        lf.EffectFacetClass.CREATE,
        repeat,
        lf.Lifetime.RUN,
        lf.InRunGroup(),
        max_attempts,
        None,
    )


def _recorded(lane: AttemptLane, root: str, result: lf.RecordedResult) -> AttemptTicket:
    """One attempt at the child's effect, applied, with its recorded result."""
    _Clock.tick()
    ticket = _issue(lane, root)
    assert isinstance(ticket, AttemptTicket), ticket
    lane.confirm(ticket, lf.Confirmation(lf.ConfirmationStatus.APPLIED, None, f"sel-{root}"))
    lane.record_result(ticket, result)
    return ticket


def _step(lane: AttemptLane, root: str, kind: lf.StepKind, code: str) -> None:
    refused = lane.record_step(
        lf.StepEntry(
            lineage=sup.lineage(*CHILD, root=root),
            at=_Clock.tick(),
            kind=kind,
            code=code,
            human_action=None,
            resend=None,
            handle=None,
        )
    )
    assert refused is None


def _view(lane: AttemptLane) -> NodeRecordView:
    return node_record_view(lane.node_record(CHILD))


def _terms(**kwargs: object) -> NodeTerms:
    return jk.terms(path=None, **kwargs)  # type: ignore[arg-type]


def _joined(lane: AttemptLane, terms: NodeTerms, observation: Observation | None = None) -> Verdict:
    return join(terms, observation, _view(lane), jk.NO_READINGS, jk.clock(now=5))


RECORDED = _terms(completion=jk.RECORDED)


@proves_other
@proves_scoped
def test_child_ignores_other_root_record(
    roots: tuple[AttemptLane, AttemptLane], tmp_path: Path
) -> None:
    r1, r2 = roots
    _recorded(r2, R2, PASSED)  # a satisfying record, under the other root
    assert _joined(r2, RECORDED).condition is Condition.SATISFIED  # it does satisfy R2's child

    # R1's NodeRecord and join input hold no entry of R2: the child of R1 is not satisfied by it
    assert _view(r1) == NodeRecordView()
    r1_entries = lf.read_lane(lf.lane_path(tmp_path / "runs" / R1)).entries
    assert not any(isinstance(e, lf.IssueEntry) for e in r1_entries)  # R1's lane holds no ticket
    r2_entries = lf.read_lane(lf.lane_path(tmp_path / "runs" / R2)).entries
    assert any(isinstance(e, lf.IssueEntry) for e in r2_entries)  # ... R2's does
    verdict = _joined(r1, RECORDED)
    assert verdict.condition is Condition.UNSATISFIED and verdict.provenance is Provenance.ABSENT

    # nor is it counted for a resource: with the environment showing it, R1's child finds it (it
    # did not create it), owns nothing, and so would never release what R2 created
    seen = jk.obs(selector_present=True)
    observed = _terms()
    mine, theirs = _joined(r1, observed, seen), _joined(r2, observed, seen)
    assert mine.provenance is Provenance.FOUND and mine.owned == ()
    assert theirs.provenance is Provenance.CREATED and len(theirs.owned) == 1

    # the other direction: a record written under R1 does not reach R2's child either
    _recorded(r1, R1, FAILED)
    assert _joined(r2, RECORDED).condition is Condition.SATISFIED
    assert all(t.result is not None and t.result.passed for t in _view(r2).tickets)


@proves_own
@proves_verify
def test_child_uses_own_root_record_until_unknowing_event(
    roots: tuple[AttemptLane, AttemptLane],
) -> None:
    r1, r2 = roots
    assert _joined(r1, RECORDED).condition is Condition.UNSATISFIED  # nothing recorded yet
    _recorded(r1, R1, PASSED)
    satisfied = _joined(r1, RECORDED)
    assert satisfied.condition is Condition.SATISFIED  # the record under R1 counts
    assert satisfied.provenance is Provenance.CREATED
    # a later record under the other root changes nothing under R1
    _recorded(r2, R2, FAILED)
    assert _joined(r1, RECORDED).condition is Condition.SATISFIED

    # it counts until a later record under R1 says otherwise: a blocked step after the latest
    # ticket (the node no longer knows), then a later attempt whose recorded result failed
    _step(r1, R1, lf.StepKind.BLOCKED, "unit.unknown_state")
    unknowing = _joined(r1, RECORDED)
    assert unknowing.condition is Condition.BLOCKED and unknowing.code == "unit.unknown_state"
    _recorded(r1, R1, FAILED)
    later = _joined(r1, RECORDED)
    assert later.condition is Condition.FAILED and later.code == "unit.tests_failed"
    # ... and the other root's own record is unmoved by any of it
    assert _joined(r2, RECORDED).condition is Condition.FAILED


@proves_once
@proves_scoped
def test_once_attempt_not_reissued_under_same_root(roots: tuple[AttemptLane, AttemptLane]) -> None:
    r1, r2 = roots
    once = _terms(repeat=jk.ONCE)
    first = _issue(r1, R1, effect="submit", repeat=lf.Repeat.ONCE)
    assert isinstance(first, AttemptTicket)

    # the environment shows nothing of it (observation cannot see the issued effect)
    unseen = jk.obs(present=False)
    assert _joined(r1, once, unseen).condition is Condition.IN_DOUBT  # the record keeps it in doubt
    refused = _issue(r1, R1, effect="submit", repeat=lf.Repeat.ONCE)
    assert refused is TicketRefusal.ONCE_ALREADY_ISSUED  # never re-issued under the same root
    assert refused.value == "once_already_issued"

    # the other root's lane has no such attempt: its own child has an issuable first attempt
    assert _joined(r2, once, unseen).condition is Condition.UNSATISFIED
    assert isinstance(_issue(r2, R2, effect="submit", repeat=lf.Repeat.ONCE), AttemptTicket)
    assert _issue(r2, R2, effect="submit", repeat=lf.Repeat.ONCE) is (
        TicketRefusal.ONCE_ALREADY_ISSUED
    )
