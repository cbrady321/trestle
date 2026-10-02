"""L.SV-5.7: the loop's converge path: both digest proofs, the dispatch re-check, plan identity
first, join / decide / advance / poll / rejoin, the loop-owned attempt measure and the held
refused steps (B1-C7, B1-C9..C11, B1-E4..E7, B2-C3, B2-C5).

Every run is a real `ChildRunServices` over a real attempt lane under a manual clock
(`loopkit`), read back through the proof oracle; only the composite test runs in memory."""

from __future__ import annotations

import ast
import json
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from tests.single.control.sweep_stub import EXE, Engine, run_sweep
from tests.single.workflow import joinkit as jk
from tests.single.workflow import loopkit as kit
from tests.single.workflow.loopkit import (
    EFFECT,
    MARKER_EFFECTS,
    RUN_EFFECT,
    Marker,
    Rig,
    Runner,
    RunPort,
    Unit,
    declaration,
    effect,
    marker_ports,
    marker_unit,
    observation,
)
from trestle.common import canonical
from trestle.common.outcome import OutcomeClass
from trestle.common.plan import bounds
from trestle.common.plan import vocabulary as vocab
from trestle.server import answer, fold, sweep
from trestle.server.sweep import CleanupDisposition
from trestle.workflow import codes, loop, ports
from trestle.workflow import services as svc
from trestle.workflow.declarations import (
    AllDeclaration,
    CompletionSource,
    Compose,
    EffectFacetClass,
    HostScopeRef,
    LoopFlags,
    RemedyDeclaration,
    Repeat,
    WorkflowEntry,
)
from trestle.workflow.units import Acted, Blocked, EffectRefused, Failed, NoAction
from trestle.workflow.values import (
    ConfirmationStatus,
    CurrencyFact,
    Goal,
    Resend,
    StopCause,
    TicketRefusal,
)

UNIT_RAISED = "execution.unit_raised"
STALE = "execution.declaration_stale"
BUDGET = "admission.budget_does_not_fit"
UNCONFIRMED = "execution.effect_unconfirmed"


def walk(rig: Rig) -> loop.Loop:
    """Run the rig's loop once and return it (for its goal)."""
    walked = rig.loop()
    walked.run()
    return walked


def absent_unit(**decl: Any) -> Unit:
    """A leaf that never finds its resource: observe says absent, advance issues nothing."""
    return Unit(
        declaration(**decl),
        lambda unit, params, reads, ctx: observation(),
        lambda unit, params, state, effects, ctx: Blocked(
            "unit.needs", "Do the thing.", Resend.SUCCEEDS_AFTER_ACTION
        ),
    )


# --------------------------------------------------------------------------- B1-E7 stops


def _declaration_stale(tmp_path: Path) -> Rig:
    marker = Marker()
    admitted = marker_unit(marker)
    other = marker_unit(marker, declaration(effects=MARKER_EFFECTS, max_attempts=4))
    rig = kit.build(tmp_path, admitted, ports=marker_ports(marker))
    rig.unit = other
    rig.entry = WorkflowEntry(root="unit", units={"unit": other}, deadline=timedelta(seconds=60))
    return rig


def _plan_stale(tmp_path: Path) -> Rig:
    marker = Marker()
    return kit.build(
        tmp_path,
        marker_unit(marker),
        ports=marker_ports(marker),
        shown=lambda plan: replace(plan, release_slice=plan.release_slice + 1.0),
    )


def _budget_short(tmp_path: Path) -> Rig:
    marker = Marker()
    rig = kit.build(tmp_path, marker_unit(marker), ports=marker_ports(marker))
    # worst case 60 s + release slice 10 s: 69 s left does not fit
    rig.clock.advance(kit.DEADLINE_S - 69.0)
    return rig


def test_declaration_digest_mismatch_declaration_stale_zero_effects(tmp_path: Path) -> None:
    rig = _declaration_stale(tmp_path)
    walk(rig)
    assert rig.classes() == ["plan", "end"]
    (end,) = rig.ends()
    assert (end["condition"], end["code"]) == ("failed", STALE)
    assert rig.unit.observes == 0 and rig.unit.advances == 0


def test_plan_digest_mismatch_declaration_stale_zero_effects(tmp_path: Path) -> None:
    rig = _plan_stale(tmp_path)
    walk(rig)
    assert rig.classes() == ["plan", "end"]
    (end,) = rig.ends()
    assert (end["condition"], end["code"]) == ("failed", STALE)
    assert rig.unit.observes == 0 and rig.unit.advances == 0


def test_a_declaration_that_no_longer_declares_is_stale(tmp_path: Path) -> None:
    rig = _declaration_stale(tmp_path)

    def broken() -> Any:
        raise RuntimeError("declare() depends on the environment")

    rig.unit.declare = broken  # type: ignore[method-assign]
    walk(rig)
    assert rig.classes() == ["plan", "end"]
    assert rig.ends()[0]["code"] == STALE


@pytest.mark.proves(
    "WR-DEADLINE-2",
    "WR-DEADLINE-2:dispatch-recheck-stops-before-effect",
    "A",
    "single",
    "PROC",
    "BOTH",
)
def test_dispatch_budget_recheck_zero_effects(tmp_path: Path) -> None:
    rig = _budget_short(tmp_path)
    walk(rig)
    assert rig.classes() == ["plan", "end"]
    (end,) = rig.ends()
    assert (end["condition"], end["code"]) == ("blocked", BUDGET)
    assert "longer deadline" in end["human_action"] and end["resend"] == "succeeds_after_action"
    assert rig.unit.observes == 0 and rig.unit.advances == 0
    assert not rig.ports[ports.ResourceReads].calls  # type: ignore[attr-defined]


def test_dispatch_recheck_boundary_exactly_fitting_proceeds(tmp_path: Path) -> None:
    marker = Marker()
    rig = kit.build(tmp_path, marker_unit(marker), ports=marker_ports(marker))
    rig.clock.advance(kit.DEADLINE_S - 70.0)  # exactly worst case + release slice
    walk(rig)
    assert rig.unit.observes >= 1


@pytest.mark.parametrize("stop", [_declaration_stale, _plan_stale, _budget_short])
def test_b1_e7_stop_writes_plan_then_root_node_end(tmp_path: Path, stop: Any) -> None:
    rig = stop(tmp_path)
    walk(rig)
    assert rig.classes() == ["plan", "end"], "record_plan, then the root's NodeEnd, no ticket"
    plan_row, end = rig.rows("plan")[0], rig.ends()[0]
    assert plan_row["selection"] == {} or plan_row["selection"] == []
    assert end["path"] == "" and end["cut"] is None and end["provenance"] is None
    assert not rig.rows("issue")


# --------------------------------------------------------------------------- plan identity


def test_record_plan_precedes_first_claim(tmp_path: Path) -> None:
    marker = Marker()
    rig = kit.build(tmp_path, marker_unit(marker), ports=marker_ports(marker), intent={"k": "v"})
    walk(rig)
    classes = rig.classes()
    assert classes[0] == "plan" and classes.index("issue") > 0
    plan_row = rig.rows("plan")[0]
    assert plan_row["declaration_digest"] == rig.plan.declaration_digest
    assert plan_row["args_hash"] == canonical.args_hash({"k": "v"})
    assert rig.rows("plan").__len__() == 1


# --------------------------------------------------------------------------- converge


def test_skip_when_postcondition_holds(tmp_path: Path) -> None:
    unit = Unit(
        declaration(),
        lambda u, p, reads, ctx: observation(found=1, ready=True),
    )
    rig = kit.build(tmp_path, unit)
    walk(rig)
    assert rig.classes() == ["plan", "end"]
    (end,) = rig.ends()
    assert (end["condition"], end["provenance"], end["cut"]) == ("satisfied", "found", None)
    assert unit.advances == 0 and unit.observes == 1


def test_advance_once_then_poll_until_satisfied(tmp_path: Path) -> None:
    marker = Marker(ready_after=2)
    unit = marker_unit(marker)
    rig = kit.build(tmp_path, unit, ports=marker_ports(marker))
    walk(rig)
    assert unit.advances == 1, "advance is never re-invoked to wait (WR-VERIFY-7)"
    assert marker.calls.count("create") == 1
    assert rig.cancel.waits == [timedelta(seconds=1)] * 2, "two polls at the declared interval"
    assert rig.classes() == [
        *("plan", "issue", "confirmation", "end"),
        *("issue", "confirmation", "released"),  # the release pass, after the NodeEnd
    ]
    (end,) = rig.ends()
    assert (end["condition"], end["provenance"]) == ("satisfied", "created")


def test_poll_waits_through_cancellation_wait(tmp_path: Path) -> None:
    marker = Marker(ready_after=1)
    rig = kit.build(tmp_path, marker_unit(marker), ports=marker_ports(marker))
    walk(rig)
    assert rig.cancel.waits, "POLL under CONVERGE waits through CancelSignal.wait"


def _runner_unit(decl: Any, port: Runner, *, on_error: Any = None) -> Unit:
    def advance(unit: Unit, params: Any, state: Any, effects: Any, ctx: Any) -> Any:
        try:
            effects.event(RunPort).run("t", RUN_EFFECT)
        except RuntimeError as exc:
            return Failed("unit.caught", str(exc))
        return Acted()

    return Unit(decl, lambda u, p, reads, ctx: observation(), advance)


class Boom(Runner):
    """An event port that raises after its ticket is durable."""

    def run(self, name: str, ticket: Any) -> Any:
        self.calls += 1
        raise RuntimeError("the port died")


def test_recorded_leaf_advance_then_rejoin_bounded_by_max_attempts(tmp_path: Path) -> None:
    decl = declaration(
        completion=CompletionSource.RECORDED,
        effects=(effect(RUN_EFFECT, EffectFacetClass.EVENT, release_timeout_s=None),),
        max_attempts=3,
    )
    port = Boom()
    unit = _runner_unit(decl, port)
    rig = kit.build(tmp_path, unit, ports={RunPort: port})
    walk(rig)
    tickets = rig.rows("issue")
    assert [t["attempt"] for t in tickets] == [1, 2, 3], "never past max_attempts"
    assert unit.advances == 3 and unit.observes == 1, "REJOIN joins from the record, no observe"
    (end,) = rig.ends()
    assert (end["condition"], end["code"]) == ("blocked", UNCONFIRMED)
    assert end["human_action"] and end["resend"] == "unknown"
    assert not rig.rows("step"), "each caught raise is evidence only (refusal precedence)"
    assert rig.sink.kinds().count("loop_step_evidence") == 3


def test_recorded_leaf_noaction_refused(tmp_path: Path) -> None:
    decl = declaration(completion=CompletionSource.RECORDED)
    unit = Unit(
        decl,
        lambda u, p, reads, ctx: observation(),
        lambda u, p, state, effects, ctx: NoAction("unit.nothing"),
    )
    rig = kit.build(tmp_path, unit)
    walk(rig)
    (step,) = rig.rows("step")
    assert (step["kind"], step["code"]) == ("failed", UNIT_RAISED)
    assert rig.ends()[0]["code"] == UNIT_RAISED
    assert unit.advances == 1


def test_readvance_bounded_by_max_attempts(tmp_path: Path) -> None:
    marker = Marker(create_status=ConfirmationStatus.NOT_APPLIED, create_code="marker.retry")
    unit = marker_unit(
        marker,
        declaration(effects=MARKER_EFFECTS, retryable=frozenset({"marker.retry"}), max_attempts=3),
    )
    rig = kit.build(tmp_path, unit, ports=marker_ports(marker))
    walk(rig)
    assert [t["attempt"] for t in rig.rows("issue")] == [1, 2, 3]
    assert unit.advances == 3
    (end,) = rig.ends()
    assert (end["condition"], end["code"]) == ("failed", "marker.retry")


def test_in_call_attempts_beyond_max_are_refused_attempts_spent(tmp_path: Path) -> None:
    marker = Marker(create_status=ConfirmationStatus.NOT_APPLIED, create_code="marker.retry")

    def greedy(unit: Unit, params: Any, state: Any, effects: Any, ctx: Any) -> Any:
        create = effects.create(ports.ResourceCreate)
        for _ in range(5):
            create.create(kit.SPEC, EFFECT)
        return Acted()

    unit = marker_unit(
        marker,
        declaration(effects=MARKER_EFFECTS, retryable=frozenset({"marker.retry"}), max_attempts=2),
        advance=greedy,
    )
    rig = kit.build(tmp_path, unit, ports=marker_ports(marker))
    walk(rig)
    assert [t["attempt"] for t in rig.rows("issue")] == [1, 2]
    assert not rig.rows("step"), "ATTEMPTS_SPENT records nothing new and is never UNIT_RAISED"


def test_an_advance_that_changes_nothing_ends_the_node_unit_raised(tmp_path: Path) -> None:
    """The loop-owned attempt measure: an ADVANCE that records nothing would repeat the same join
    forever, so it ends the node UNIT_RAISED instead."""

    def dry(u: Unit, p: Any, state: Any, effects: Any, ctx: Any) -> Any:
        raise EffectRefused(TicketRefusal.ONCE_ALREADY_ISSUED)

    unit = Unit(declaration(), lambda u, p, reads, ctx: observation(), dry)
    rig = kit.build(tmp_path, unit)
    walk(rig)
    assert unit.advances == 1
    assert [(s["kind"], s["code"]) for s in rig.rows("step")] == [("failed", UNIT_RAISED)]


def test_caught_refusal_returned_step_is_evidence_only(tmp_path: Path) -> None:
    """Refusal precedence (V-3.7): a unit that catches the facet's refusal and returns a step
    changes nothing; the next join reads what the facet recorded and the tickets."""
    marker = Marker(create_status=ConfirmationStatus.NOT_APPLIED, create_code="marker.retry")

    def catches(u: Unit, p: Any, state: Any, effects: Any, ctx: Any) -> Any:
        create = effects.create(ports.ResourceCreate)
        create.create(kit.SPEC, EFFECT)  # attempt 1 of 1: NOT_APPLIED
        try:
            create.create(kit.SPEC, EFFECT)  # attempt 2: ATTEMPTS_SPENT
        except EffectRefused:
            return Failed("unit.caught", "swallowed the refusal")
        return Acted()

    unit = marker_unit(
        marker,
        declaration(effects=MARKER_EFFECTS, retryable=frozenset({"marker.retry"}), max_attempts=1),
        advance=catches,
    )
    rig = kit.build(tmp_path, unit, ports=marker_ports(marker))
    walk(rig)
    assert not rig.rows("step"), "the returned Failed was not recorded"
    assert rig.sink.kinds().count("loop_step_evidence") == 1
    assert rig.ends()[0]["code"] == "marker.retry", "the ticket's row decides (J-5)"


def test_ticketless_readvance_under_a_granted_remedy_is_unit_raised(tmp_path: Path) -> None:
    marker = Marker(ready_after=10**6)  # created, never ready: J-20 grants the declared remedy
    decl = declaration(
        effects=(*MARKER_EFFECTS, effect("restart", EffectFacetClass.OWNED)),
        remedies=(
            RemedyDeclaration(
                codes.POSTCONDITION_TIMEOUT, "restart", 2, timedelta(seconds=30), timedelta(0)
            ),
        ),
        max_attempts=1,
        max_wait_s=3.0,
    )
    seen: list[Any] = []

    def advance(u: Unit, p: Any, state: Any, effects: Any, ctx: Any) -> Any:
        seen.append((state.remedy, ctx.remedy))
        create = effects.create(ports.ResourceCreate)
        try:
            create.create(kit.SPEC, EFFECT)  # re-requests the refused effect instead of the remedy
        except EffectRefused:
            return Failed("unit.caught", "gave up")
        return Acted()

    unit = marker_unit(marker, decl, advance=advance)
    rig = kit.build(tmp_path, unit, ports=marker_ports(marker))
    walk(rig)
    granted = seen[-1]
    assert granted[0] is not None and granted[0] == granted[1], "the grant reaches ActContext"
    assert granted[0].effect == "restart"
    assert [(s["kind"], s["code"]) for s in rig.rows("step")] == [("failed", UNIT_RAISED)]


def test_operator_bounds_come_from_the_services(tmp_path: Path) -> None:
    rig = kit.build(tmp_path, Unit(declaration()))
    assert rig.loop().currency_margin == timedelta(seconds=kit.MARGIN_S)

    entry = WorkflowEntry(
        root="unit", units={"unit": Unit(declaration())}, deadline=timedelta(seconds=60)
    )
    plain = loop.Loop(kit.MemoryServices(kit.admit(entry)), entry, {})
    assert plain.currency_margin == timedelta(0), "a RunServices without the bounds reads zero"


def test_acted_without_a_ticket_is_unit_raised(tmp_path: Path) -> None:
    unit = Unit(
        declaration(),
        lambda u, p, reads, ctx: observation(),
        lambda u, p, state, effects, ctx: Acted(),
    )
    rig = kit.build(tmp_path, unit)
    walk(rig)
    (step,) = rig.rows("step")
    assert (step["kind"], step["code"]) == ("failed", UNIT_RAISED)
    assert unit.advances == 1


def test_uncaught_raise_out_of_advance_flips_then_records_unit_raised(tmp_path: Path) -> None:
    def advance(u: Unit, p: Any, state: Any, effects: Any, ctx: Any) -> Any:
        raise KeyError("boom")

    unit = Unit(declaration(), lambda u, p, reads, ctx: observation(), advance)
    rig = kit.build(tmp_path, unit)
    walk(rig)
    (step,) = rig.rows("step")
    assert (step["kind"], step["code"]) == ("failed", UNIT_RAISED)
    assert rig.ends()[0]["code"] == UNIT_RAISED
    assert "loop_unit_raised" in rig.sink.kinds()


def test_malformed_observation_is_unit_raised(tmp_path: Path) -> None:
    # a declared precondition with no check at all is L.SL-7.2's uncovered stop (see
    # registration/test_precondition_coverage.py); a check the declaration never listed is malformed
    unit = Unit(
        declaration(preconditions=("pre0",)),
        lambda u, p, reads, ctx: observation(pre=(True, True), preconditions=("pre0", "extra")),
    )
    rig = kit.build(tmp_path, unit)
    walk(rig)
    assert rig.ends()[0]["code"] == UNIT_RAISED and unit.advances == 0


# --------------------------------------------------------------------------- receipt checks


def _blocked(code: str, human_action: str) -> Unit:
    return Unit(
        declaration(),
        lambda u, p, reads, ctx: observation(),
        lambda u, p, state, effects, ctx: Blocked(code, human_action, Resend.SUCCEEDS_AFTER_ACTION),
    )


def _lane_text(rig: Rig) -> str:
    return (rig.run_dir / "evidence" / "lane.ndjson").read_text(encoding="utf-8")


def test_overlong_human_action_recorded_unit_raised(tmp_path: Path) -> None:
    text = "x" * (bounds.HUMAN_ACTION_MAX + 1)
    rig = kit.build(tmp_path, _blocked("unit.needs", text))
    walk(rig)
    (step,) = rig.rows("step")
    assert (step["kind"], step["code"], step["human_action"]) == ("failed", UNIT_RAISED, None)
    assert "xxxxxxxx" not in _lane_text(rig), "the text is neither recorded nor truncated"
    (end,) = rig.ends()
    assert end["code"] == UNIT_RAISED and end["cut"] is None


def test_overlong_code_recorded_unit_raised(tmp_path: Path) -> None:
    rig = kit.build(tmp_path, _blocked("c" * (bounds.CODE_MAX + 1), "Do the thing."))
    walk(rig)
    (step,) = rig.rows("step")
    assert (step["kind"], step["code"]) == ("failed", UNIT_RAISED)
    assert "cccccccc" not in _lane_text(rig)
    assert rig.ends()[0]["code"] == UNIT_RAISED


def test_blocked_without_a_human_action_is_unit_raised(tmp_path: Path) -> None:
    rig = kit.build(tmp_path, _blocked("unit.needs", ""))
    walk(rig)
    assert [(s["kind"], s["code"]) for s in rig.rows("step")] == [("failed", UNIT_RAISED)]


def test_human_action_at_bound_recorded_byte_identical(tmp_path: Path) -> None:
    text = '"' * (bounds.HUMAN_ACTION_MAX // 2)  # each quote is escaped: exactly the bound
    assert bounds.text_bytes(text) == bounds.HUMAN_ACTION_MAX
    rig = kit.build(tmp_path, _blocked("unit.needs", text))
    walk(rig)
    (step,) = rig.rows("step")
    assert (step["kind"], step["code"], step["human_action"]) == ("blocked", "unit.needs", text)
    (end,) = rig.ends()
    assert (end["condition"], end["human_action"]) == ("blocked", text)


def test_failed_detail_goes_to_evidence_never_the_lane(tmp_path: Path) -> None:
    unit = Unit(
        declaration(),
        lambda u, p, reads, ctx: observation(),
        lambda u, p, state, effects, ctx: Failed("unit.broke", "the private detail"),
    )
    rig = kit.build(tmp_path, unit)
    walk(rig)
    assert "the private detail" not in _lane_text(rig)
    assert any(
        kind == "loop_failed_detail" and fields["detail"] == "the private detail"
        for kind, fields in rig.sink.events
    )
    assert rig.rows("step")[0]["code"] == "unit.broke"


# --------------------------------------------------------------------------- composite


def test_childless_composite_root_ends_normally_without_effect() -> None:
    """The composite raise (TM-B2-6) is gone with the in-library walk (L.TR-3.2): a composite
    root with nothing to walk writes its plan and its own `NodeEnd` (ended normally: no condition,
    no cut) and issues nothing."""
    root = AllDeclaration(
        unit="root",
        flags=LoopFlags(Compose.ALL, CompletionSource.OBSERVED, Repeat.SAFE),
        children=(),
        concurrency=1,
        budget=timedelta(seconds=60),
        identifier_sets={},
        arg_bindings=(),
        env_key_field=None,
    )
    entry = WorkflowEntry(root="root", units={"root": root}, deadline=timedelta(seconds=60))
    services = kit.MemoryServices(kit.admit(entry))
    loop.Loop(services, entry, {}).run()
    assert [name for name, _ in services.lane.calls] == ["record_plan", "record_end"]
    end = services.lane.calls[1][1]
    assert (end.condition, end.code, end.cut) == (None, None, None)


def test_walk_children_probe_string_is_gone_from_loop_py() -> None:
    """TM-B2-6's probe, `grep -q 'walk_children: tree band' trestle/workflow/loop.py`, reports
    absent once the in-library `AllDeclaration` walk landed (L.TR-3.2, DM-42)."""
    source = (Path(loop.__file__)).read_text(encoding="utf-8")
    assert "walk_children: tree band" not in source


# --------------------------------------------------------------------------- held steps


def _fixture_engine_descriptor() -> ports.ArgvRelease:
    return ports.ArgvRelease(
        executable=EXE,
        observe_argv=("obs", EFFECT),
        observe_ok_exit=frozenset({0}),
        stop_argv=("stop", EFFECT),
        timeout=timedelta(seconds=2),
        remove_argv=("rm", EFFECT),
    )


def test_full_lane_node_blocked_refused_full_not_overflowed(tmp_path: Path) -> None:
    """A one-vertex fixture on the in-memory marker, a real lane whose capacity is lowered so the
    node's second issue is refused: the node stops BLOCKED through J-1 on its held step; the lane
    holds the vertex's NodeEnd and no such step; the real fold reports refused_full and not
    overflowed; the first ticket's created marker, which the full lane also kept the release from
    stopping, is disposed by the real sweep from its descriptor (B2-C9: refused_full alone
    changes nothing)."""
    marker = Marker(descriptor=_fixture_engine_descriptor())

    def two_effects(u: Unit, p: Any, state: Any, effects: Any, ctx: Any) -> Any:
        create = effects.create(ports.ResourceCreate)
        create.create(kit.SPEC, EFFECT)  # ticket 1 fits
        create.create(kit.SPEC, "up2")  # the lane is full: refused, the step is held
        return Acted()

    decl = declaration(
        effects=(*MARKER_EFFECTS, effect("up2", EffectFacetClass.CREATE)), max_wait_s=5.0
    )
    unit = marker_unit(marker, decl, advance=two_effects)
    rig = kit.build(tmp_path, unit, ports=marker_ports(marker), lane_entries=6)
    walk(rig)

    (end,) = rig.ends()
    assert (end["condition"], end["code"], end["cut"]) == ("blocked", codes.LANE_UNAVAILABLE, None)
    assert "disk space" in end["human_action"] and end["resend"] == "unknown"
    assert [t["effect"] for t in rig.rows("issue")] == [EFFECT], "the second issue was refused"
    assert not rig.rows("step"), "the held steps never reached the lane"
    assert rig.classes() == ["plan", "issue", "confirmation", "end"]
    assert marker.stops == [], "the release could not issue its ticket either"

    folded = fold.fold_lane(rig.run_dir, rig.plan)
    assert folded.refused_full is True and folded.overflowed is False and folded.ended is True
    assert not fold.cleanup_is_unknown(folded)
    engine = Engine(present={EFFECT})
    disposition = run_sweep(folded, engine, plan=rig.plan)
    assert [t.effect for t in disposition.released] == [EFFECT, None]
    assert disposition.unknown == ()
    assert engine.roles(EFFECT)[:3] == ["obs", "stop", "rm"]


def test_lane_full_at_a_step_holds_it_in_process(tmp_path: Path) -> None:
    """The loop's own steps go through `record_step`; FULL keeps one in process and the join
    reads it (V-4.5), while the NodeEnd still carries the condition."""
    unit = _blocked("unit.needs", "Do the thing.")
    rig = kit.build(tmp_path, unit, lane_entries=2)  # plan + one end slot: no room for a step
    walk(rig)
    assert rig.classes() == ["plan", "end"], "the step was refused FULL and held, never written"
    (end,) = rig.ends()
    assert (end["condition"], end["code"], end["human_action"]) == (
        "blocked",
        "unit.needs",
        "Do the thing.",
    )
    assert json.loads(_lane_text(rig).splitlines()[-1])["class"] == "end"


# =========================================================================== L.SV-5.8
# goal flip, release pass, carve step, one NodeEnd per vertex, run_tree returns None


class StepSpy:
    """A lane that reports the loop's goal at each `record_step`, then delegates."""

    def __init__(self, walked: loop.Loop) -> None:
        self._walked = walked
        self._real = walked.lane
        self.seen: list[tuple[Goal, str]] = []

    def record_step(self, step: Any) -> Any:
        self.seen.append((self._walked.goal, step.code))
        return self._real.record_step(step)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


def created_never_ready(**knobs: Any) -> tuple[Marker, Unit]:
    marker = Marker(ready_after=10**6, **knobs)
    return marker, marker_unit(marker)


def test_cancel_mid_poll_flips_goal_release_walk(tmp_path: Path) -> None:
    marker, unit = created_never_ready()
    rig = kit.build(tmp_path, unit, ports=marker_ports(marker))
    rig.cancel.stop_on_wait = 2  # the cancel arrives while the loop waits the second interval
    walk(rig)
    # observed at start and once after the first poll; the third is the release's absence check
    assert unit.advances == 1 and unit.observes == 3, (
        "no observation between the stop and the release"
    )
    (end,) = rig.ends()
    assert (end["condition"], end["cut"], end["provenance"]) == ("converging", "stopped", "created")
    assert marker.stops == [EFFECT], "the release walk still gave the created resource back"
    assert rig.classes()[-3:] == ["issue", "confirmation", "released"]
    assert len(rig.cancel.waits) == 2, "the RELEASE wait is a plain clock wait, not the cancel wait"
    assert rig.sleeps, "and it ran on the plain clock"


def test_release_point_flips_goal(tmp_path: Path) -> None:
    marker, unit = created_never_ready()
    rig = kit.build(tmp_path, unit, ports=marker_ports(marker))
    rig.cancel.stop_cause = StopCause.RELEASE_POINT
    rig.cancel.stop_on_wait = 1
    walk(rig)
    (end,) = rig.ends()
    assert (end["condition"], end["cut"]) == ("converging", "stopped")
    assert marker.stops == [EFFECT] and rig.classes()[-1] == "released"


def test_uncaught_exception_flips_before_unit_raised_step(tmp_path: Path) -> None:
    def advance(u: Unit, p: Any, state: Any, effects: Any, ctx: Any) -> Any:
        raise KeyError("boom")

    unit = Unit(declaration(), lambda u, p, reads, ctx: observation(), advance)
    rig = kit.build(tmp_path, unit)
    walked = rig.loop()
    spy = StepSpy(walked)
    walked.lane = spy  # type: ignore[assignment]
    walked.run()
    assert spy.seen == [(Goal.RELEASE, UNIT_RAISED)], "the flip precedes the step (B1-E6)"
    (end,) = rig.ends()
    assert (end["condition"], end["code"], end["cut"]) == ("failed", UNIT_RAISED, None)


def test_release_pass_on_done(tmp_path: Path) -> None:
    marker = Marker(ready_after=0)
    rig = kit.build(tmp_path, marker_unit(marker), ports=marker_ports(marker))
    walk(rig)
    assert rig.ends()[0]["condition"] == "satisfied"
    assert marker.stops == [EFFECT] and not marker.present
    (released,) = rig.rows("released")
    assert released["effect"] == EFFECT
    assert rig.classes().index("released") > rig.classes().index("end")


def test_release_reverse_issue_then_observed_absence_then_record(tmp_path: Path) -> None:
    marker = Marker(ready_after=0, absent_after=1)

    def two(u: Unit, p: Any, state: Any, effects: Any, ctx: Any) -> Any:
        create = effects.create(ports.ResourceCreate)
        create.create(kit.SPEC, EFFECT)
        create.create(kit.SPEC, "up2")
        return Acted()

    decl = declaration(effects=(*MARKER_EFFECTS, effect("up2", EffectFacetClass.CREATE)))
    unit = marker_unit(marker, decl, advance=two)

    def observe(u: Unit, p: Any, reads: Any, ctx: Any) -> Any:
        """The node's observation, as a unit with two instances would make it: while a release is
        under way, whether the instance being released is still there."""
        reads.read(ports.ResourceReads).observe(kit.SPEC, ctx.lineage, EFFECT)
        present = marker.stops[-1] in marker.live if marker.stops else marker.present
        return observation(selector_present=present, ready=True)

    unit._observe = observe
    rig = kit.build(tmp_path, unit, ports=marker_ports(marker))
    walked = rig.loop()
    real = walked.lane
    at_record: list[tuple[str, bool]] = []
    original = real.record_released

    def spy(handle: Any, outcome: Any) -> None:
        at_record.append((handle.effect, handle.effect in marker.live))
        original(handle, outcome)

    real.record_released = spy  # type: ignore[method-assign]
    walked.run()
    assert marker.stops == ["up2", "up"], "reverse issue order"
    assert at_record == [("up2", False), ("up", False)], "each recorded only after its absence"
    assert [r["effect"] for r in rig.rows("released")] == ["up2", "up"]
    # one handle at a time: release, poll until absent, record, then the next
    assert rig.classes()[-6:] == [
        *("issue", "confirmation", "released"),
        *("issue", "confirmation", "released"),
    ]


def test_handle_not_observed_absent_left_to_sweep(tmp_path: Path) -> None:
    marker = Marker(ready_after=0, stop_holds=True)  # the stop was accepted; it never goes away
    rig = kit.build(tmp_path, marker_unit(marker), ports=marker_ports(marker))
    walk(rig)
    assert marker.stops == [EFFECT]
    assert not rig.rows("released"), "never reported released by the loop (WR-OWN-6)"
    assert sum(rig.sleeps) == pytest.approx(2.0), "bounded by the effect's release_timeout"
    folded = fold.fold_lane(rig.run_dir, rig.plan)
    assert [t.released_at for t in folded.entries] == [None, None]
    assert folded.ended is True


def test_release_wait_is_bounded_by_the_root_deadline(tmp_path: Path) -> None:
    marker = Marker(ready_after=0, stop_holds=True)
    rig = kit.build(tmp_path, marker_unit(marker), ports=marker_ports(marker))
    walked = rig.loop()
    # only 0.5 s of the root deadline are left when the release begins
    unit_release = rig.unit._release

    def late(u: Unit, p: Any, handle: Any, effects: Any, ctx: Any) -> Any:
        rig.clock.now = kit.NOW + timedelta(seconds=kit.DEADLINE_S - 0.5)
        return unit_release(u, p, handle, effects, ctx)  # type: ignore[misc]

    rig.unit._release = late
    walked.run()
    assert sum(rig.sleeps) == pytest.approx(0.5)


def test_release_call_raise_is_the_handles_cleanup_outcome(tmp_path: Path) -> None:
    marker = Marker(ready_after=0)

    def bad(u: Unit, p: Any, handle: Any, effects: Any, ctx: Any) -> Any:
        raise RuntimeError("release blew up")

    unit = marker_unit(marker)
    unit._release = bad
    rig = kit.build(tmp_path, unit, ports=marker_ports(marker))
    walk(rig)
    steps = rig.rows("step")
    assert [(s["kind"], s["code"]) for s in steps] == [("failed", UNIT_RAISED)]
    assert rig.ends()[0]["condition"] == "satisfied", "never the node's condition (V-3.7)"


def test_root_slice_end_flips_the_goal_no_carve_step(tmp_path: Path) -> None:
    """P4: the root is carved nothing, so its slice end is the release point and flips the goal:
    the walk is cut (`STOPPED`, the last verdict) and releases, with no `CARVE_EXCEEDED` step.
    The carve itself (step, then a join) is a carved node's: `tests/tree/test_tr3_slices.py`."""
    marker, unit = created_never_ready()
    rig = kit.build(tmp_path, unit, ports=marker_ports(marker), slice_end_s=5.0)
    walked = walk(rig)
    assert walked.goal is Goal.RELEASE
    assert not [s for s in rig.rows("step") if s["code"] == codes.CARVE_EXCEEDED]
    (end,) = rig.ends()
    assert (end["condition"], end["cut"], end["provenance"]) == ("converging", "stopped", "created")
    assert end["code"] != codes.CARVE_EXCEEDED
    assert unit.observes >= 5


def test_stale_at_slice_end_joins_once_more_and_yields_currency_unconfirmed(
    tmp_path: Path,
) -> None:
    fact = CurrencyFact(HostScopeRef.DEMO_CREDENTIAL, "g1", None)
    unit = Unit(
        declaration(),
        lambda u, p, reads, ctx: observation(found=1, ready=True, currency=(fact,)),
    )
    rig = kit.build(tmp_path, unit, slice_end_s=4.0)
    walk(rig)
    assert not rig.rows("step"), "STALE joins once more: no CARVE_EXCEEDED step"
    (end,) = rig.ends()
    assert (end["condition"], end["code"]) == ("blocked", codes.CURRENCY_UNCONFIRMED)
    assert end["human_action"] and end["resend"] == "succeeds_after_action"


def _currency_rig(tmp_path: Path, scope: object | None) -> tuple[Rig, Unit]:
    """A ready leaf whose observation carries the demo credential at generation g1; the loop
    reads the host scope through `scope` (`HostScopeReads`), or with none bound."""
    fact = CurrencyFact(HostScopeRef.DEMO_CREDENTIAL, "g1", None)
    unit = Unit(
        declaration(),
        lambda u, p, reads, ctx: observation(found=1, ready=True, currency=(fact,)),
    )
    bound = {} if scope is None else {ports.HostScopeReads: scope}
    return kit.build(tmp_path, unit, ports=bound, slice_end_s=4.0), unit


def _scope_events(rig: Rig) -> list[Any]:
    return [fields for kind, fields in rig.sink.events if kind == "loop_host_scope"]


def test_the_loop_reads_the_host_scope_and_a_current_fact_is_satisfied(tmp_path: Path) -> None:
    """V-9.6: the loop reads the subject the fact names through the bound reader; equal
    generations join SATISFIED. One reading per observation, recorded as one evidence event."""
    rig, unit = _currency_rig(tmp_path, jk.StaticScope((HostScopeRef.DEMO_CREDENTIAL, "g1")))
    walk(rig)
    (end,) = rig.ends()
    assert (end["condition"], end["code"]) == ("satisfied", None)
    events = _scope_events(rig)
    assert len(events) == unit.observes
    assert events[0]["read"] == {"demo_credential": "g1"} and events[0]["unread"] == {}


@pytest.mark.parametrize("bound", ["unreadable", "none"])
def test_an_unread_subject_joins_stale_then_currency_unconfirmed(
    tmp_path: Path, bound: str
) -> None:
    """V-9.7, B3-C18: an unreadable subject, or no reader bound, gives no reading; the fact joins
    as if it differed (STALE) and the slice end yields CURRENCY_UNCONFIRMED."""
    scope = jk.StaticScope() if bound == "unreadable" else None
    rig, unit = _currency_rig(tmp_path, scope)
    walk(rig)
    (end,) = rig.ends()
    assert (end["condition"], end["code"]) == ("blocked", codes.CURRENCY_UNCONFIRMED)
    events = _scope_events(rig)
    assert len(events) == unit.observes
    want = vocab.HOST_SCOPE_UNREADABLE if bound == "unreadable" else None
    assert events[0]["read"] == {} and events[0]["unread"] == {"demo_credential": want}


@pytest.mark.parametrize(
    ("unit_factory", "condition", "present"),
    [
        (
            lambda: Unit(declaration(), lambda u, p, r, c: observation(found=1, ready=True)),
            "satisfied",
            False,
        ),
        (lambda: _blocked("unit.needs", "Do the thing."), "blocked", True),
        (
            lambda: Unit(
                declaration(),
                lambda u, p, r, c: observation(),
                lambda u, p, s, e, c: Failed("unit.broke", "detail"),
            ),
            "failed",
            False,
        ),
    ],
)
def test_one_node_end_per_vertex_with_presence_rule(
    tmp_path: Path, unit_factory: Any, condition: str, present: bool
) -> None:
    rig = kit.build(tmp_path, unit_factory())
    walk(rig)
    (end,) = rig.ends()  # exactly one
    assert end["condition"] == condition
    assert (end["human_action"] is not None) is present
    assert (end["resend"] is not None) is present


def test_presence_rule_by_code_postcondition_timeout(tmp_path: Path) -> None:
    marker, unit = created_never_ready()
    unit.decl = declaration(effects=MARKER_EFFECTS, max_wait_s=3.0)
    rig = kit.build(tmp_path, unit, ports=marker_ports(marker))
    walk(rig)
    (end,) = rig.ends()
    assert (end["condition"], end["code"]) == ("failed", codes.POSTCONDITION_TIMEOUT)
    assert end["human_action"] and end["resend"] == "unknown", "present by the code, not the class"


def test_stopped_vertex_node_end_cut_stopped(tmp_path: Path) -> None:
    """A stop that arrives while a ticket is being issued: the facet records NOT_APPLIED
    STOP_SEEN, the goal flips, and the node is STOPPED, not a failure candidate (B1-E4, B1-C11)."""
    marker = Marker()

    def stop_then_create(u: Unit, p: Any, state: Any, effects: Any, ctx: Any) -> Any:
        rig.cancel.stop = StopCause.CANCEL
        effects.create(ports.ResourceCreate).create(kit.SPEC, EFFECT)
        return Acted()

    unit = marker_unit(marker, advance=stop_then_create)
    rig = kit.build(tmp_path, unit, ports=marker_ports(marker))
    walk(rig)
    (conf,) = rig.rows("confirmation")
    assert (conf["status"], conf["code"]) == ("not_applied", codes.STOP_SEEN)
    assert "create" not in marker.calls, "no port call after the stop"
    (end,) = rig.ends()
    assert end["cut"] == "stopped"


def test_not_started_vertex_node_end(tmp_path: Path) -> None:
    marker = Marker()
    rig = kit.build(tmp_path, marker_unit(marker), ports=marker_ports(marker))
    rig.cancel.stop = StopCause.CANCEL  # the root stopped before its first ticket
    walk(rig)
    assert rig.classes() == ["plan", "end"]
    (end,) = rig.ends()
    assert (end["cut"], end["condition"], end["code"], end["provenance"]) == (
        "not_started",
        None,
        None,
        None,
    )
    assert end["human_action"] is None and end["resend"] is None
    assert rig.unit.observes == 0


def test_record_end_unavailable_vertex_unended(tmp_path: Path) -> None:
    """`record_end` UNAVAILABLE leaves the vertex unended; the loop neither raises nor retries, and
    the host answers EXECUTION_ERROR VERTEX_UNENDED (B4-C2 rule (4))."""
    unit = Unit(declaration(), lambda u, p, r, c: observation(found=1, ready=True))
    rig = kit.build(tmp_path, unit)
    walked = rig.loop()
    real = walked.lane
    refusals: list[Any] = []

    class Unavailable:
        def record_end(self, end: Any) -> Any:
            refusals.append(end)
            return svc.LaneRefusal.UNAVAILABLE

        def __getattr__(self, name: str) -> Any:
            return getattr(real, name)

    walked.lane = Unavailable()  # type: ignore[assignment]
    walked.run()
    assert len(refusals) == 1 and rig.ends() == []
    folded = fold.fold_lane(rig.run_dir, rig.plan)
    assert folded.ended is False
    got = answer.project(
        folded,
        CleanupDisposition(released=(sweep.GROUP_TARGET,)),
        type("Gone", (), {"confirmed_gone": True})(),
        rig.plan,
        False,
        None,
    )
    assert got.outcome is OutcomeClass.EXECUTION_ERROR
    assert got.error is not None and got.error.code == vocab.VERTEX_UNENDED


def test_a_second_node_end_is_a_loop_defect(tmp_path: Path) -> None:
    unit = Unit(declaration(), lambda u, p, r, c: observation(found=1, ready=True))
    rig = kit.build(tmp_path, unit)
    walked = rig.loop()
    walked.run()
    with pytest.raises(RuntimeError, match="duplicate"):
        walked.end_vertex(loop.ROOT, condition=None, code=None)


def test_run_tree_returns_none(tmp_path: Path) -> None:
    marker = Marker()
    rig = kit.build(tmp_path, marker_unit(marker), ports=marker_ports(marker))

    class Ctx:
        run_services = rig.services

    assert loop.run_tree(Ctx(), rig.entry, rig.intent, ports=rig.ports) is None  # type: ignore[arg-type]
    assert rig.ends()[0]["condition"] == "satisfied"


def test_loop_imports_no_precedence() -> None:
    """B4-I4: trestle.workflow never imports trestle.common.plan.precedence."""
    tree = ast.parse(Path(loop.__file__).read_text(encoding="utf-8"))
    imported = {n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | {
        a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names
    }
    assert not any("precedence" in name for name in imported)
