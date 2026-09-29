"""L.SV-5.7: the loop's converge path: both digest proofs, the dispatch re-check, plan identity
first, join / decide / advance / poll / rejoin, the loop-owned attempt measure and the held
refused steps (B1-C7, B1-C9..C11, B1-E4..E7, B2-C3, B2-C5).

Every run is a real `ChildRunServices` over a real attempt lane under a manual clock
(`loopkit`), read back through the proof oracle; only the composite test runs in memory."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from tests.single.control.sweep_stub import EXE, Engine, run_sweep
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
from trestle.common.plan import bounds
from trestle.server import fold
from trestle.workflow import codes, loop, ports
from trestle.workflow.declarations import (
    AllDeclaration,
    CompletionSource,
    Compose,
    EffectFacetClass,
    LoopFlags,
    RemedyDeclaration,
    Repeat,
    WorkflowEntry,
)
from trestle.workflow.units import Acted, Blocked, EffectRefused, Failed, NoAction
from trestle.workflow.values import ConfirmationStatus, Goal, Resend, TicketRefusal

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
    assert rig.classes() == ["plan", "issue", "confirmation", "end"]
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
    walked = walk(rig)
    (step,) = rig.rows("step")
    assert (step["kind"], step["code"]) == ("failed", UNIT_RAISED)
    assert rig.ends()[0]["code"] == UNIT_RAISED and walked.goal is Goal.RELEASE
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
    walked = walk(rig)
    assert [t["attempt"] for t in rig.rows("issue")] == [1, 2]
    assert not rig.rows("step"), "ATTEMPTS_SPENT records nothing new and is never UNIT_RAISED"
    assert walked.goal is Goal.CONVERGE


def test_an_advance_that_changes_nothing_ends_the_node_unit_raised(tmp_path: Path) -> None:
    """The loop-owned attempt measure: an ADVANCE that records nothing would repeat the same join
    forever, so it ends the node UNIT_RAISED instead."""

    def dry(u: Unit, p: Any, state: Any, effects: Any, ctx: Any) -> Any:
        raise EffectRefused(TicketRefusal.ONCE_ALREADY_ISSUED)

    unit = Unit(declaration(), lambda u, p, reads, ctx: observation(), dry)
    rig = kit.build(tmp_path, unit)
    walked = walk(rig)
    assert unit.advances == 1 and walked.goal is Goal.RELEASE
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
                codes.POSTCONDITION_TIMEOUT, "restart", 2, timedelta(seconds=300), timedelta(0)
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
    walked = walk(rig)
    granted = seen[-1]
    assert granted[0] is not None and granted[0] == granted[1], "the grant reaches ActContext"
    assert granted[0].effect == "restart"
    assert [(s["kind"], s["code"]) for s in rig.rows("step")] == [("failed", UNIT_RAISED)]
    assert walked.goal is Goal.RELEASE


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
    walked = walk(rig)
    (step,) = rig.rows("step")
    assert (step["kind"], step["code"]) == ("failed", UNIT_RAISED)
    assert walked.goal is Goal.RELEASE and unit.advances == 1


def test_uncaught_raise_out_of_advance_flips_then_records_unit_raised(tmp_path: Path) -> None:
    def advance(u: Unit, p: Any, state: Any, effects: Any, ctx: Any) -> Any:
        raise KeyError("boom")

    unit = Unit(declaration(), lambda u, p, reads, ctx: observation(), advance)
    rig = kit.build(tmp_path, unit)
    walked = walk(rig)
    (step,) = rig.rows("step")
    assert (step["kind"], step["code"]) == ("failed", UNIT_RAISED)
    assert walked.goal is Goal.RELEASE
    assert rig.ends()[0]["code"] == UNIT_RAISED
    assert "loop_unit_raised" in rig.sink.kinds()


def test_malformed_observation_is_unit_raised(tmp_path: Path) -> None:
    unit = Unit(
        declaration(preconditions=("pre0",)),
        lambda u, p, reads, ctx: observation(pre=()),  # the declaration lists pre0
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
    walked = walk(rig)
    (step,) = rig.rows("step")
    assert (step["kind"], step["code"], step["human_action"]) == ("failed", UNIT_RAISED, None)
    assert "xxxxxxxx" not in _lane_text(rig), "the text is neither recorded nor truncated"
    (end,) = rig.ends()
    assert end["code"] == UNIT_RAISED and end["cut"] is None
    assert walked.goal is Goal.RELEASE


def test_overlong_code_recorded_unit_raised(tmp_path: Path) -> None:
    rig = kit.build(tmp_path, _blocked("c" * (bounds.CODE_MAX + 1), "Do the thing."))
    walked = walk(rig)
    (step,) = rig.rows("step")
    assert (step["kind"], step["code"]) == ("failed", UNIT_RAISED)
    assert "cccccccc" not in _lane_text(rig)
    assert rig.ends()[0]["code"] == UNIT_RAISED and walked.goal is Goal.RELEASE


def test_blocked_without_a_human_action_is_unit_raised(tmp_path: Path) -> None:
    rig = kit.build(tmp_path, _blocked("unit.needs", ""))
    walk(rig)
    assert [(s["kind"], s["code"]) for s in rig.rows("step")] == [("failed", UNIT_RAISED)]


def test_human_action_at_bound_recorded_byte_identical(tmp_path: Path) -> None:
    text = '"' * (bounds.HUMAN_ACTION_MAX // 2)  # each quote is escaped: exactly the bound
    assert bounds.text_bytes(text) == bounds.HUMAN_ACTION_MAX
    rig = kit.build(tmp_path, _blocked("unit.needs", text))
    walked = walk(rig)
    (step,) = rig.rows("step")
    assert (step["kind"], step["code"], step["human_action"]) == ("blocked", "unit.needs", text)
    (end,) = rig.ends()
    assert (end["condition"], end["human_action"]) == ("blocked", text)
    assert walked.goal is Goal.CONVERGE


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


def test_composite_vertex_raises_before_effect() -> None:
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
    with pytest.raises(loop.TreeBandError, match="walk_children: tree band"):
        loop.Loop(services, entry, {}).run()
    assert [name for name, _ in services.lane.calls] == ["record_plan"], "no effect, no NodeEnd"


def test_walk_children_probe_string_is_in_loop_py() -> None:
    """TM-B2-6's probe: `grep -q 'walk_children: tree band' trestle/workflow/loop.py`."""
    source = (Path(loop.__file__)).read_text(encoding="utf-8")
    assert "walk_children: tree band" in source


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
    overflowed; the first ticket's created marker is disposed by the real sweep from its
    descriptor (B2-C9: refused_full alone changes nothing)."""
    marker = Marker(vanish_after_create=True, descriptor=_fixture_engine_descriptor())
    unit = marker_unit(
        marker,
        declaration(effects=MARKER_EFFECTS, max_wait_s=5.0, poll_s=1.0, max_attempts=3),
    )
    rig = kit.build(tmp_path, unit, ports=marker_ports(marker), lane_entries=6)
    walk(rig)

    (end,) = rig.ends()
    assert (end["condition"], end["code"], end["cut"]) == ("blocked", codes.LANE_UNAVAILABLE, None)
    assert "disk space" in end["human_action"] and end["resend"] == "unknown"
    assert [t["attempt"] for t in rig.rows("issue")] == [1], "the second issue was refused"
    assert not [s for s in rig.rows("step")], "the held step never reached the lane"
    assert rig.classes() == ["plan", "issue", "confirmation", "end"]

    folded = fold.fold_lane(rig.run_dir, rig.plan)
    assert folded.refused_full is True and folded.overflowed is False and folded.ended is True
    assert not fold.cleanup_is_unknown(folded)
    engine = Engine(present={EFFECT})
    disposition = run_sweep(folded, engine, plan=rig.plan)
    assert [t.effect for t in disposition.released] == [EFFECT, None]
    assert not disposition.unknown or disposition.unknown == ()
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
