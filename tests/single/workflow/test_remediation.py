"""L.SL-6.1: remedy only for a declared code -> action pair, bounded, always re-verified, never
without progress (V-3.1 J-3 and J-3a; B1-C3 `ActContext.remedy`, B1-C10 POLL; WR-REMEDY-1,
WR-REMEDY-2, WR-REMEDY-3; V-3.6 group 5a).

Two levels. The join's own rows (`join`, pure) over records built by hand say what J-3 and J-3a
match. The loop rows run the published fixture `tests/fixtures/workflows/remedy_leaf.py` over its
fake marker (unready, reporting `marker.hot`, until restarted) through the real `run_tree` under the
loop tests' manual clock (`loopkit`): the clock only moves when the loop waits, so every wait, every
cooldown and every bound is read exactly and nothing sleeps."""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest
from trestle_packs.fakes import ConfirmationStatus

from tests.single.workflow import loopkit as kit
from tests.single.workflow.joinkit import (
    APPLIED,
    NOT_APPLIED,
    TIMEOUT,
    obs,
    record,
    terms,
    ticket,
)
from tests.single.workflow.loopkit import Rig
from tests.single.workflow.test_join_table import run, shape
from trestle.common.plan import precedence
from trestle.common.plan.vocabulary import NodeClass, ResourceDisposition
from trestle.workflow import codes, human_actions, ports
from trestle.workflow.declarations import RemedyDeclaration
from trestle.workflow.values import Condition as C
from trestle.workflow.values import Provenance as P
from trestle.workflow.values import RemedyGrant

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "workflows" / "remedy_leaf.py"
HOT = "marker.hot"


def _fixture() -> ModuleType:
    spec = importlib.util.spec_from_file_location("remedy_leaf_fixture", FIXTURE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


def _rig(
    tmp_path: Path,
    *,
    attempts: int = 2,
    total_s: float = 6.0,
    cooldown_s: float = 0.5,
    max_attempts: int = 3,
    fixed: bool = False,
    lag: int = 1,
    trigger: str = HOT,
    restart: ConfirmationStatus = ConfirmationStatus.APPLIED,
    retryable: frozenset[str] = frozenset(),
) -> tuple[Rig, Any, Any]:
    fixture = _fixture()
    unit = fixture.make_unit(
        fixture.declaration(
            remedy_attempts=attempts,
            remedy_total_s=total_s,
            cooldown_s=cooldown_s,
            max_attempts=max_attempts,
            retryable=retryable,
            env_key_field=None,
        )
    )
    marker = fixture.RemedyMarker(
        tmp_path / "markers",
        "run",
        lag_polls=lag,
        never_ready=fixed,
        fixed_fingerprint=fixed,
        trigger=trigger,
        restart_status=restart,
        restart_code=fixture.RETRYABLE,
    )
    rig = kit.build(
        tmp_path,
        unit,
        ports={
            ports.ResourceReads: marker,
            ports.ResourceCreate: marker,
            ports.ResourceOwned: marker,
        },
    )
    return rig, unit, marker


def _remedy_tickets(rig: Rig) -> list[dict[str, Any]]:
    return [t for t in rig.rows("issue") if t["remedy"] is not None]


def _at(row: dict[str, Any]) -> datetime:
    return datetime.fromisoformat(row["issued_at"].replace("Z", "+00:00"))


# ------------------------------------------------------------------------------ declared pair only


@pytest.mark.proves("WR-REMEDY-1", "WR-REMEDY-1:declared-pair-only", "A", "single", "LOGIC", "CI")
def test_undeclared_code_no_remedy(tmp_path: Path) -> None:
    """The marker reports a code the declaration lists no remedy for: the loop never grants one
    (WR-REMEDY-1), never restarts, and the node fails on the wait's timeout like any unready
    resource."""
    rig, unit, marker = _rig(tmp_path, trigger="marker.other")
    rig.run()
    assert _remedy_tickets(rig) == [] and marker.restarts == 0
    assert "remedy" not in unit.log
    (end,) = rig.ends()
    assert (end["condition"], end["code"]) == ("failed", codes.POSTCONDITION_TIMEOUT)


def test_join_grants_only_the_declared_pair() -> None:
    """The verdict's grant is the one declared `RemedyDeclaration` for the trigger code: the
    observation's code, then POSTCONDITION_TIMEOUT; a code with no declaration gives none."""
    t = terms(remedies=(RemedyDeclaration(HOT, "fix", 2, timedelta(300), timedelta(1)),))
    owned = record(tickets=(ticket(status=APPLIED, with_handle=True),))
    v = run(t, obs(selector_present=True, post=False, code=HOT), owned, now=10)
    assert shape(v) == (P.CREATED, C.UNSATISFIED, None) and v.remedy == RemedyGrant(HOT, "fix", 1)
    v = run(t, obs(selector_present=True, post=False, code="marker.other"), owned, now=10)
    assert shape(v) == (P.CREATED, C.FAILED, TIMEOUT) and v.remedy is None


# ------------------------------------------------------------------------------ bounds


def test_remedy_bounded_attempts_total_cooldown(tmp_path: Path) -> None:
    """A remedy is bounded by its declared attempts, its total time and its cooldown, whichever
    comes first; every remedy ticket is one attempt. The restart is refused (NOT_APPLIED, a code
    the leaf declares retryable) so the loop goes on to the next grant."""
    busy = frozenset({"marker.busy"})
    # attempts: two remedy tickets, then BLOCKED remedy_exhausted (J-3), never a third
    rig, _, marker = _rig(
        tmp_path / "attempts",
        attempts=2,
        max_attempts=6,
        restart=ConfirmationStatus.NOT_APPLIED,
        retryable=busy,
    )
    rig.run()
    assert len(_remedy_tickets(rig)) == 2 and marker.restarts == 2
    assert [t["remedy"]["attempt"] for t in _remedy_tickets(rig)] == [1, 2]
    (end,) = rig.ends()
    assert (end["condition"], end["code"]) == ("blocked", codes.REMEDY_EXHAUSTED)

    # total: the declared attempts are not spent, the total time is
    rig, _, marker = _rig(
        tmp_path / "total",
        attempts=5,
        total_s=1.5,
        max_attempts=9,
        restart=ConfirmationStatus.NOT_APPLIED,
        retryable=busy,
    )
    rig.run()
    tickets = _remedy_tickets(rig)
    assert 1 <= len(tickets) < 5 and marker.restarts == len(tickets)
    (end,) = rig.ends()
    assert (end["condition"], end["code"]) == ("blocked", codes.REMEDY_EXHAUSTED)
    assert rig.clock.now >= _at(tickets[0]) + timedelta(seconds=1.5)

    # cooldown: it keeps two tickets of one remedy `cooldown` apart (the loop waits it out) ...
    rig, _, _ = _rig(
        tmp_path / "cooldown",
        attempts=2,
        cooldown_s=5.0,
        total_s=30.0,  # wait 1 + remedies 30 + release 2 fits the leaf's budget (L.SL-2.1)
        max_attempts=6,
        restart=ConfirmationStatus.NOT_APPLIED,
        retryable=busy,
    )
    rig.run()
    first, second = _remedy_tickets(rig)
    assert _at(second) - _at(first) >= timedelta(seconds=5.0)
    assert any(w > timedelta(seconds=1.0) for w in rig.cancel.waits), "the cooldown was waited"

    # ... and a cooldown that no longer fits inside the total spends the remedy after one ticket
    rig, _, marker = _rig(
        tmp_path / "cooldown-spent",
        attempts=3,
        cooldown_s=5.0,
        total_s=4.0,
        max_attempts=6,
        restart=ConfirmationStatus.NOT_APPLIED,
        retryable=busy,
    )
    rig.run()
    assert len(_remedy_tickets(rig)) == 1 and marker.restarts == 1
    assert rig.ends()[0]["code"] == codes.REMEDY_EXHAUSTED


def test_every_remedy_followed_by_postcondition_check(tmp_path: Path) -> None:
    """After every remedy ticket the loop observes before it joins or acts again (V-3.5, B1-C10
    POLL): the unit's own call log never shows a remedy followed by anything but an observe."""
    for fixed in (False, True):
        rig, unit, _ = _rig(tmp_path / str(fixed), fixed=fixed, attempts=2)
        rig.run()
        log = unit.log
        assert "remedy" in log
        for index, entry in enumerate(log):
            if entry == "remedy":
                assert log[index + 1] == "observe", log
        tickets = _remedy_tickets(rig)
        assert len(tickets) >= 1
        # the stop ticket of the release pass comes after the last observation (NodeEnd first)
        assert log[-1] == "observe" or log[-1] == "release"


# ------------------------------------------------------------------------------ J-3a, J-3


def test_trigger_code_persisting_after_wait_is_remedy_no_progress(tmp_path: Path) -> None:
    """FakeMarker(fixed_fingerprint=True): the repair is confirmed and the trigger code is still
    reported once the ticket's wait has elapsed: J-3a gives BLOCKED REMEDY_NO_PROGRESS after one
    remedy ticket (a second attempt was still declared), with V-11.1's human action and re-send."""
    rig, unit, marker = _rig(tmp_path, fixed=True, attempts=2)
    rig.run()
    (remedy_ticket,) = _remedy_tickets(rig)
    assert marker.restarts == 1
    (end,) = rig.ends()
    assert (end["condition"], end["code"]) == ("blocked", codes.REMEDY_NO_PROGRESS)
    text, resend = human_actions.render(
        codes.REMEDY_NO_PROGRESS, path="(root)", effect="restart", subject="(unspecified)"
    )
    assert end["human_action"] == text and end["resend"] == resend.value
    # J-3a waited the confirmed remedy ticket's wait (max_wait 1.0) before it matched
    stop = next(t for t in rig.rows("issue") if t["effect"] == "stop")
    assert _at(stop) - _at(remedy_ticket) >= timedelta(seconds=1.0)


def test_join_no_progress_rows() -> None:
    """J-3a, joined by hand: the latest ticket is a confirmed remedy ticket for `c`, its wait has
    elapsed, and the verdict groups 3-5 produced still carries `c`."""
    decl = RemedyDeclaration(HOT, "fix", 3, timedelta(300), timedelta(1))
    t = terms(remedies=(decl,), max_wait=10)
    fixed = ticket(
        attempt=2, issued=1, status=APPLIED, effect="fix", remedy_grant=RemedyGrant(HOT, "fix", 1)
    )
    rec = record(tickets=(ticket(status=APPLIED, with_handle=True), fixed))
    still = obs(selector_present=True, post=False, code=HOT)
    # within the ticket's wait: converging (J-19)
    assert run(t, still, rec, now=10).condition is C.CONVERGING
    # wait elapsed, the trigger persists: no progress (J-3a)
    assert shape(run(t, still, rec, now=11)) == (P.CREATED, C.BLOCKED, codes.REMEDY_NO_PROGRESS)
    # wait elapsed, the code changed and no remedy is declared for the new one: not J-3a
    moved = obs(selector_present=True, post=False, code="marker.other")
    assert shape(run(t, moved, rec, now=11)) == (P.CREATED, C.FAILED, TIMEOUT)
    # a repaired resource is SATISFIED, never no-progress
    assert run(t, obs(selector_present=True), rec, now=11).condition is C.SATISFIED
    # an unconfirmed remedy ticket is not J-3a's
    unconfirmed = ticket(
        attempt=2, issued=1, status=None, effect="fix", remedy_grant=RemedyGrant(HOT, "fix", 1)
    )
    rec_open = record(tickets=(ticket(status=APPLIED, with_handle=True), unconfirmed))
    assert run(t, still, rec_open, now=11).code != codes.REMEDY_NO_PROGRESS


def test_exhausted_blocked_remedy_exhausted(tmp_path: Path) -> None:
    """The remedy's only attempt is spent and the resource is still unhealthy: J-3 (which precedes
    J-3a) gives BLOCKED REMEDY_EXHAUSTED with V-11.1's human action and re-send."""
    rig, _, marker = _rig(tmp_path, fixed=True, attempts=1)
    rig.run()
    assert len(_remedy_tickets(rig)) == 1 and marker.restarts == 1
    (end,) = rig.ends()
    assert (end["condition"], end["code"]) == ("blocked", codes.REMEDY_EXHAUSTED)
    text, resend = human_actions.render(
        codes.REMEDY_EXHAUSTED, path="(root)", effect="restart", subject="(unspecified)"
    )
    assert end["human_action"] == text and end["resend"] == resend.value


def test_exhaustion_not_counted_before_latest_remedy_wait_elapsed(tmp_path: Path) -> None:
    """The last attempt is ticketed but its wait is running: the node is CONVERGING (J-19), not
    exhausted, until the wait has elapsed or the ticket resolved NOT_APPLIED (J-3)."""
    decl = RemedyDeclaration(HOT, "fix", 1, timedelta(300), timedelta(1))
    t = terms(remedies=(decl,), max_wait=10)
    remedy_ticket = ticket(
        attempt=2, issued=1, status=APPLIED, effect="fix", remedy_grant=RemedyGrant(HOT, "fix", 1)
    )
    rec = record(tickets=(ticket(status=APPLIED, with_handle=True), remedy_ticket))
    still = obs(selector_present=True, post=False, code=HOT)
    assert run(t, still, rec, now=5).condition is C.CONVERGING
    assert run(t, still, rec, now=10).condition is C.CONVERGING
    assert shape(run(t, still, rec, now=11)) == (P.CREATED, C.BLOCKED, codes.REMEDY_EXHAUSTED)
    # a ticket the port declined is settled at once: nothing is left to wait for
    declined = ticket(
        attempt=2,
        issued=1,
        status=NOT_APPLIED,
        code="marker.other",
        effect="fix",
        remedy_grant=RemedyGrant(HOT, "fix", 1),
    )
    rec_declined = record(tickets=(ticket(status=APPLIED, with_handle=True), declined))
    assert run(t, still, rec_declined, now=2).code == codes.REMEDY_EXHAUSTED
    # ... and on the loop: the exhaustion verdict comes only after the wait ran its course
    rig, _, _ = _rig(tmp_path, fixed=True, attempts=1)
    rig.run()
    (issued,) = _remedy_tickets(rig)
    stop = next(t for t in rig.rows("issue") if t["effect"] == "stop")
    assert _at(stop) - _at(issued) >= timedelta(seconds=1.0)


def test_join_group_5a_never_matches_a_satisfied_verdict() -> None:
    """J-3's guard: a repair that succeeds on its last attempt joins SATISFIED (J-18), not
    exhausted, even though every attempt is ticketed."""
    decl = RemedyDeclaration(HOT, "fix", 1, timedelta(300), timedelta(1))
    t = terms(remedies=(decl,), max_wait=10)
    remedy_ticket = ticket(
        attempt=2, issued=1, status=APPLIED, effect="fix", remedy_grant=RemedyGrant(HOT, "fix", 1)
    )
    rec = record(tickets=(ticket(status=APPLIED, with_handle=True), remedy_ticket))
    assert run(t, obs(selector_present=True), rec, now=30).condition is C.SATISFIED
    # a NoAction step (J-15) is only reached when neither J-3 nor J-3a matched
    assert run(t, obs(selector_present=True, post=False, code=HOT), rec, now=1).condition in (
        C.CONVERGING,
    )


def test_repair_succeeding_on_last_attempt_is_repaired(tmp_path: Path) -> None:
    """The one declared attempt is the repair and it works: the node joins SATISFIED (never
    exhausted or no progress), and B4-C3 answers it repaired: class REPAIRED, resource disposition
    REPAIRED."""
    rig, _, marker = _rig(tmp_path, attempts=1, fixed=False, lag=1)
    rig.run()
    assert len(_remedy_tickets(rig)) == 1 and marker.restarts == 1
    (end,) = rig.ends()
    assert (end["condition"], end["code"]) == ("satisfied", None)
    tickets = [
        SimpleNamespace(
            remedy=t["remedy"],
            confirmation=SimpleNamespace(status=c["status"]),
            effect=t["effect"],
        )
        for t in rig.rows("issue")
        for c in rig.rows("confirmation")
        if c["effect"] == t["effect"] and c["attempt"] == t["attempt"]
    ]
    end_like = SimpleNamespace(
        condition=end["condition"], code=end["code"], cut=end["cut"], provenance=end["provenance"]
    )
    assert precedence.node_class(end_like, tickets) is NodeClass.REPAIRED
    assert precedence.resource_disposition(end_like, tickets) is ResourceDisposition.REPAIRED
    # the same run without a remedy ticket is PASSED, so REPAIRED is the remedy's alone
    plain = [t for t in tickets if t.remedy is None]
    assert precedence.node_class(end_like, plain) is NodeClass.PASSED
