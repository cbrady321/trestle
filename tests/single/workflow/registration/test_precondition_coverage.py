"""L.SL-7.2: in-node precondition refusal (B1-E1 at run time, J-24; DM-34, A2.4).

At one vertex a declared precondition is covered only by an inline check the node's own `observe`
returns. A precondition with no inline check stops the node before the first claim with
`execution.plan_precondition_uncovered`: one `plan` entry, one `step`, the vertex's `end`, no
`issue`, no port call. An unsatisfied precondition is the join's J-24 (`precondition_unsatisfied`,
BLOCKED). Both are answered as exactly one decision-table class, never `PASSED` (DM-34)."""

from __future__ import annotations

from dataclasses import replace
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
    marker_ports,
    marker_unit,
    observation,
)
from trestle.common.outcome import OutcomeClass
from trestle.common.plan import vocabulary as vocab
from trestle.server import answer, fold
from trestle.server.sweep import CleanupDisposition
from trestle.workflow import codes
from trestle.workflow.values import CheckResult

UNCOVERED = "execution.plan_precondition_uncovered"
UNSATISFIED = "execution.precondition_unsatisfied"


def _rig(
    tmp_path: Path, marker: Marker, *, checks: tuple[bool, ...], names: tuple[str, ...]
) -> Rig:
    """A marker unit that declares `names` as preconditions and whose `observe` returns the inline
    checks in `checks` (paired with `names` in order; fewer checks than names leaves the rest
    uncovered)."""
    base = marker_unit(marker, declaration(effects=MARKER_EFFECTS, preconditions=names))

    def observe(unit: Unit, params: Any, reads: Any, ctx: Any) -> Any:
        seen = base.observe(params, reads, ctx)
        return replace(
            seen,
            preconditions=tuple(
                (n, CheckResult(ok, None, "")) for n, ok in zip(names, checks, strict=False)
            ),
        )

    unit = Unit(base.decl, observe, base._advance, base._release)
    return kit.build(tmp_path, unit, ports=marker_ports(marker))


def _class_of(rig: Rig) -> OutcomeClass:
    folded = fold.fold_lane(rig.run_dir, rig.plan)
    got = answer.project(
        folded,
        CleanupDisposition(released=()),
        type("Gone", (), {"confirmed_gone": True})(),
        rig.plan,
        False,
        None,
    )
    return got.outcome


def test_uncovered_precondition_stops_before_the_first_claim(tmp_path: Path) -> None:
    marker = Marker()
    rig = _rig(tmp_path, marker, checks=(), names=("toolchain",))
    rig.run()
    assert rig.classes() == ["plan", "step", "end"], rig.classes()
    (step,) = rig.rows("step")
    assert (step["kind"], step["code"]) == ("failed", UNCOVERED)
    (end,) = rig.ends()
    assert (end["condition"], end["code"], end["cut"]) == ("failed", UNCOVERED, None)
    assert end["human_action"] is None
    assert not rig.rows("issue") and not rig.rows("confirmation"), "zero claim entries"
    assert "create" not in marker.calls and "stop" not in marker.calls, marker.calls
    assert rig.unit.advances == 0 and rig.unit.releases == 0
    assert "loop_precondition_uncovered" in rig.sink.kinds()


def test_partly_covered_names_the_missing_check_and_stops(tmp_path: Path) -> None:
    marker = Marker()
    rig = _rig(tmp_path, marker, checks=(True,), names=("a", "b"))
    rig.run()
    (end,) = rig.ends()
    assert end["code"] == UNCOVERED and not rig.rows("issue") and rig.unit.advances == 0
    (event,) = [f for k, f in rig.sink.events if k == "loop_precondition_uncovered"]
    assert event["preconditions"] == ["b"]


def test_uncovered_precondition_is_answered_as_one_class_never_passed(tmp_path: Path) -> None:
    rig = _rig(tmp_path, Marker(), checks=(), names=("toolchain",))
    rig.run()
    klass = _class_of(rig)
    assert klass is not OutcomeClass.PASSED
    assert klass is OutcomeClass.FAILED  # the one class the B4-T2 table gives FAILED
    assert vocab.PLAN_PRECONDITION_UNCOVERED == codes.PLAN_PRECONDITION_UNCOVERED == UNCOVERED


def test_covered_satisfied_precondition_is_unchanged(tmp_path: Path) -> None:
    marker = Marker()
    rig = _rig(tmp_path, marker, checks=(True,), names=("toolchain",))
    rig.run()
    assert rig.rows("issue"), "a covered, satisfied precondition proceeds to the claim"
    assert rig.rows("issue")[0]["effect"] == EFFECT
    assert all(e["code"] != UNCOVERED for e in rig.rows("step"))
    assert _class_of(rig) is OutcomeClass.PASSED


def test_unsatisfied_precondition_blocks_zero_effects(tmp_path: Path) -> None:
    marker = Marker()
    rig = _rig(tmp_path, marker, checks=(False,), names=("toolchain",))
    rig.run()
    (end,) = rig.ends()
    assert (end["condition"], end["code"]) == ("blocked", UNSATISFIED), end
    assert end["human_action"] and end["resend"] == "succeeds_after_action"
    assert not rig.rows("issue") and not rig.rows("confirmation"), "zero claim entries"
    assert "create" not in marker.calls, marker.calls
    assert _class_of(rig) is OutcomeClass.BLOCKED


def test_extra_check_or_wrong_type_is_still_a_malformed_observation(tmp_path: Path) -> None:
    """Coverage concerns only a declared precondition with no check; every other malformed
    observation stays UNIT_RAISED (B1-C2)."""
    unit = Unit(
        declaration(preconditions=("pre0",)),
        lambda u, p, reads, ctx: observation(pre=(True, True), preconditions=("pre0", "extra")),
    )
    rig = kit.build(tmp_path, unit)
    rig.run()
    assert rig.ends()[0]["code"] == codes.UNIT_RAISED and unit.advances == 0


def test_later_observation_that_drops_a_check_is_unit_raised_not_uncovered(tmp_path: Path) -> None:
    """Only the opening observation, the one before any claim, stops as uncovered; a check that
    vanishes after the claim is the unit breaking its contract (B1-C2, B1-E6)."""
    marker = Marker(ready_after=5)
    base = marker_unit(marker, declaration(effects=MARKER_EFFECTS, preconditions=("pre0",)))
    seen = {"n": 0}

    def observe(unit: Unit, params: Any, reads: Any, ctx: Any) -> Any:
        seen["n"] += 1
        got = base.observe(params, reads, ctx)
        if seen["n"] == 1:
            return replace(got, preconditions=(("pre0", CheckResult(True, None, "")),))
        return replace(got, preconditions=())

    unit = Unit(base.decl, observe, base._advance, base._release)
    rig = kit.build(tmp_path, unit, ports=marker_ports(marker))
    rig.run()
    assert len(rig.rows("issue")) >= 1, "the first observation covered the precondition"
    (end,) = rig.ends()
    assert end["code"] == codes.UNIT_RAISED != UNCOVERED


@pytest.mark.parametrize("names", [("a",), ("a", "b")])
def test_no_ticket_is_ever_issued_when_uncovered(tmp_path: Path, names: tuple[str, ...]) -> None:
    rig = _rig(tmp_path, Marker(), checks=(), names=names)
    rig.run()
    assert "issue" not in rig.classes()
