"""L.SL-4.1: retry only on declared codes, within attempts and time; a ONCE step is never
re-issued once it may have taken effect (A3.2, WR-IDEM-3; B1-E2, V-3.1 J-4 / J-5 / J-8 / J-17a).

Every run is a real `ChildRunServices` over a real attempt lane under a manual clock (`loopkit`),
read back through the proof oracle. The join is the one producer of a verdict; the loop only
re-advances when that verdict is UNSATISFIED, and the lane bounds every ticket by `max_attempts`
and refuses a second ticket for a ONCE effect."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.single.workflow import loopkit as kit
from tests.single.workflow.loopkit import (
    EFFECT,
    MARKER_EFFECTS,
    Marker,
    Rig,
    Unit,
    declaration,
    effect,
    marker_ports,
    marker_unit,
)
from trestle.workflow import codes, ports
from trestle.workflow.declarations import EffectFacetClass, Lifetime, Repeat
from trestle.workflow.units import Acted
from trestle.workflow.values import Confirmation, ConfirmationStatus

RETRYABLE = "marker.retry"
OTHER = "marker.other"


class Flaky(Marker):
    """A marker whose `create` answers `first` for its first `fails` calls, then really creates."""

    def __init__(
        self, *, fails: int, status: ConfirmationStatus = ConfirmationStatus.NOT_APPLIED, code: str
    ) -> None:
        super().__init__()
        self._fails, self._status, self._code = fails, status, code
        self.creates = 0

    def create(self, spec: Any, ticket: Any) -> Confirmation:
        self.creates += 1
        if self.creates <= self._fails:
            self.calls.append("create")
            return Confirmation(self._status, self._code, None)
        return super().create(spec, ticket)


def _run(tmp_path: Path, marker: Marker, **decl: Any) -> tuple[Rig, Unit]:
    unit = marker_unit(marker, declaration(effects=MARKER_EFFECTS, **decl))
    rig = kit.build(tmp_path, unit, ports=marker_ports(marker))
    rig.run()
    return rig, unit


def _attempts(rig: Rig) -> list[int]:
    """The create effect's attempt numbers (the release's own stop ticket is not one of them)."""
    return [t["attempt"] for t in rig.rows("issue") if t["effect"] == EFFECT]


def test_declared_code_retried_within_budget(tmp_path: Path) -> None:
    marker = Flaky(fails=2, code=RETRYABLE)
    rig, unit = _run(tmp_path, marker, retryable=frozenset({RETRYABLE}), max_attempts=3)
    assert _attempts(rig) == [1, 2, 3], "two transient failures cured by the third attempt"
    statuses = [
        (c["status"], c.get("code")) for c in rig.rows("confirmation") if c["effect"] == EFFECT
    ]
    assert statuses == [
        ("not_applied", RETRYABLE),
        ("not_applied", RETRYABLE),
        ("applied", None),
    ]
    (end,) = rig.ends()
    assert (end["condition"], end["code"]) == ("satisfied", None)
    assert unit.advances == 3
    assert not rig.rows("step"), "a retry is a ticket, not a step"


def test_undeclared_code_never_retried(tmp_path: Path) -> None:
    marker = Flaky(fails=1, code=OTHER)
    rig, unit = _run(tmp_path, marker, retryable=frozenset({RETRYABLE}), max_attempts=3)
    assert _attempts(rig) == [1], "an undeclared code issues no new claim"
    assert unit.advances == 1 and marker.creates == 1
    (end,) = rig.ends()
    assert (end["condition"], end["code"]) == ("failed", OTHER)


def test_no_retryable_declared_means_no_retry(tmp_path: Path) -> None:
    marker = Flaky(fails=1, code=RETRYABLE)
    rig, _ = _run(tmp_path, marker, retryable=frozenset(), max_attempts=3)
    assert _attempts(rig) == [1]
    assert rig.ends()[0]["code"] == RETRYABLE and rig.ends()[0]["condition"] == "failed"


@pytest.mark.parametrize("max_attempts", [1, 2, 4])
def test_attempts_never_exceed_max(tmp_path: Path, max_attempts: int) -> None:
    marker = Flaky(fails=99, code=RETRYABLE)
    rig, unit = _run(tmp_path, marker, retryable=frozenset({RETRYABLE}), max_attempts=max_attempts)
    assert _attempts(rig) == list(range(1, max_attempts + 1))
    assert marker.creates == max_attempts and unit.advances == max_attempts
    (end,) = rig.ends()
    assert (end["condition"], end["code"]) == ("failed", RETRYABLE), "spent, with the port's code"


def _once_unit(marker: Marker, *, max_attempts: int = 3) -> Unit:
    """A ONCE leaf: it declares no release effect (B1-E1), so it creates a DURABLE resource."""
    decl = declaration(
        repeat=Repeat.ONCE,
        effects=(effect(EFFECT, EffectFacetClass.CREATE, lifetime=Lifetime.DURABLE),),
        retryable=frozenset({RETRYABLE}),
        max_attempts=max_attempts,
    )
    return marker_unit(marker, decl)


@pytest.mark.proves("WR-IDEM-3", "WR-IDEM-3:once-never-reissued", "A", "single", "LOGIC", "CI")
def test_once_never_reissued_after_may_have_taken_effect(tmp_path: Path) -> None:
    marker = Flaky(fails=99, status=ConfirmationStatus.UNKNOWN, code=RETRYABLE)
    unit = _once_unit(marker)
    rig = kit.build(tmp_path, unit, ports=marker_ports(marker))
    rig.run()
    assert _attempts(rig) == [1], "a ONCE effect that may have taken effect is never issued again"
    assert marker.creates == 1 and unit.advances == 1
    (end,) = rig.ends()
    assert (end["condition"], end["code"]) == ("blocked", codes.EFFECT_UNCONFIRMED)
    assert end["human_action"] and end["resend"] == "unknown", "the operator decides (V-3.7)"


def test_once_is_retried_when_the_port_says_it_never_took_effect(tmp_path: Path) -> None:
    """NOT_APPLIED is a resolved 'no effect', so even a ONCE effect may go again on a declared
    retryable code (J-4); only UNKNOWN closes the door."""
    marker = Flaky(fails=1, code=RETRYABLE)
    unit = _once_unit(marker)
    rig = kit.build(tmp_path, unit, ports=marker_ports(marker))
    rig.run()
    assert _attempts(rig) == [1, 2]
    assert rig.ends()[0]["condition"] == "satisfied"


def test_once_second_ticket_after_unknown_is_refused_by_the_lane(tmp_path: Path) -> None:
    """Even a unit that ignores the join and issues again is refused: the lane, not the unit's
    good behaviour, holds the ONCE guarantee (B1-E4, `TicketRefusal.ONCE_ALREADY_ISSUED`)."""
    marker = Flaky(fails=99, status=ConfirmationStatus.UNKNOWN, code=RETRYABLE)

    def greedy(u: Unit, p: Any, state: Any, effects: Any, ctx: Any) -> Any:
        create = effects.create(ports.ResourceCreate)
        for _ in range(3):
            create.create(kit.SPEC, EFFECT)
        return Acted()

    decl = declaration(
        repeat=Repeat.ONCE,
        effects=(effect(EFFECT, EffectFacetClass.CREATE, lifetime=Lifetime.DURABLE),),
        max_attempts=3,
    )
    unit = marker_unit(marker, decl, advance=greedy)
    rig = kit.build(tmp_path, unit, ports=marker_ports(marker))
    rig.run()
    assert _attempts(rig) == [1] and marker.creates == 1
