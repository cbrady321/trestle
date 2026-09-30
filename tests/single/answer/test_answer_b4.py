"""L.SV-4.2: the host answer (B4-C1..C8) over the folded record: each B4-C2 rule decides the case
built for it, in the order (1), (2), (5), (3), (4); the primary, the listing, the required fields,
the bound and the carrier, the cleanup composition; the run view carries the answer beside today's
run state and recomputes it from the durable inputs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.core.spine import support
from tests.proof.foundations import trees
from tests.single.answer import builders as b
from trestle.common import codes
from trestle.common import lane_format as lf
from trestle.common.fsutil import atomic_write_json
from trestle.common.outcome import OutcomeClass, classify
from trestle.common.plan import bounds, carving
from trestle.common.plan import precedence as prec
from trestle.common.plan import vocabulary as vocab
from trestle.common.plan.compiler import AdmittedPlan, compile
from trestle.common.plan.vocabulary import Listing, NodeClass, ResourceDisposition, RootStop
from trestle.common.types import RunView
from trestle.server import answer, fold, sweep
from trestle.server.ledger import RunLedger, evidence_dir, ledger_path
from trestle.server.main import create_kernel
from trestle.server.recovery import recover_run_dir, seed_interrupted_run
from trestle.server.sweep import CleanupDisposition

C = lf.Condition
P = vocab.V11
CANCEL, RELEASE = fold.CAUSE_CANCEL, fold.CAUSE_RELEASE_POINT


def _err(code: str, phase: str = "x", message: str = "m") -> answer.ExecutionErrorAnswer:
    return answer.ExecutionErrorAnswer(code, phase, message)


# -- rule (1): a stop row decides the class ---------------------------------------------------


@pytest.mark.proves(
    "WR-TERM-3", "WR-TERM-3:stop-row-cause-decides-class", "A", "single", "MCP+PROC+STUB", "CI"
)
def test_rule1_first_stop_row_cause_decides_class() -> None:
    plan = b.make_plan()
    ends = (b.satisfied("n1"), b.end("n2", C.FAILED, P["UNIT_RAISED"]), b.satisfied(""))
    for stops, outcome, stop in (
        ((CANCEL, RELEASE), OutcomeClass.CANCELLED, RootStop.CANCEL),
        ((RELEASE, CANCEL), OutcomeClass.TIMED_OUT, RootStop.RELEASE_POINT),
    ):
        # the lane holds every NodeEnd (a fold that `ended`): the stop row still decides, and the
        # nodes' own conditions stay listed, never the primary
        got = b.project(b.folded(*ends, stops=stops, plan=plan), plan)
        assert (got.outcome, got.root_stop) == (outcome, stop)
        assert got.error is None and got.primary.path == ()
    # no matter what CancelSignal.cause() said in-run: only the rows count
    only_release = b.project(b.folded(stops=(RELEASE,), plan=plan, ended=False), plan)
    assert only_release.outcome is OutcomeClass.TIMED_OUT


def test_rule1_release_point_incomplete_set() -> None:
    plan = b.make_plan(("all", (trees.LEAF, trees.LEAF, trees.LEAF)))
    # n1 stopped mid-condition, n2 has entries but no NodeEnd, n3 ended
    stopped = b.end("n1", C.CONVERGING, cut=lf.Cut.STOPPED)
    unended_entry = b.ticket("n2", confirmation=b.APPLIED)
    got = b.project(
        b.folded(stopped, b.satisfied("n3"), entries=(unended_entry,), stops=(RELEASE,), plan=plan),
        plan,
    )
    assert got.outcome is OutcomeClass.TIMED_OUT
    # the timed-out stage: the lowest-ordinal STOPPED or UNENDED vertex, decisive and inline
    assert got.incomplete == ("n1",) and got.error is None
    listings = {n.path: n.listing for n in got.listed}
    assert listings[("n1",)] is Listing.STOPPED and listings[("n2",)] is Listing.UNENDED
    # nothing stopped or unended: the primary's own path; a plan-less root: ()
    ended = b.project(
        b.folded(b.satisfied("n1"), b.satisfied(""), stops=(RELEASE,), plan=plan), plan
    )
    assert ended.incomplete == ended.primary.path
    planless = b.project(b.folded(stops=(RELEASE,), plan_entry=False), None)
    assert planless.incomplete == () and planless.primary.listing is Listing.STOPPED


# -- rule (2): a restart ------------------------------------------------------------------------


def test_rule2_restart_without_stop_execution_restart() -> None:
    plan = b.make_plan()
    record = b.folded(b.satisfied("n1"), plan=plan, ended=False)
    got = b.project(record, plan, recovered=True, error=_err(vocab.EXECUTION_RESTART, "recovery"))
    assert (got.outcome, got.root_stop, got.recovered) == (
        OutcomeClass.EXECUTION_ERROR,
        RootStop.RESTART,
        True,
    )
    assert got.error is not None and got.error.code == "execution.interrupted"
    assert got.primary.path == () and got.incomplete is None
    # the same for a plan-less root (B4-T4 `interrupted`)
    planless = b.project(b.folded(plan_entry=False), None, recovered=True)
    assert planless.root_stop is RootStop.RESTART
    assert planless.error is not None and planless.error.code == vocab.EXECUTION_RESTART


def test_rule1_restart_after_stop_keeps_cause_recovered() -> None:
    plan = b.make_plan()
    record = b.folded(b.satisfied("n1"), stops=(CANCEL,), plan=plan, ended=False)
    got = b.project(record, plan, recovered=True, error=_err(vocab.EXECUTION_RESTART))
    assert got.outcome is OutcomeClass.CANCELLED and got.root_stop is RootStop.CANCEL
    assert got.recovered is True and got.error is None  # the stop's class, not the restart's


# -- rule (5): a plan-less root (B4-T4) -----------------------------------------------------------


def test_rule5_planless_root_b4_t4_equals_classify() -> None:
    rows = {
        "succeeded": None,
        "failed": {"code": codes.EXECUTION_PLUGIN_RAISED, "phase": "run", "message": "boom"},
        "worker_exit": {"code": codes.EXECUTION_WORKER_EXIT, "phase": "exit", "message": "gone"},
        "cancelled": None,
        "timed_out": None,
        "interrupted": {"code": codes.EXECUTION_INTERRUPTED, "phase": "recovery", "message": "r"},
    }
    for kind, row in rows.items():
        stops = {"cancelled": (CANCEL,), "timed_out": (RELEASE,)}.get(kind, ())
        record = b.folded(stops=stops, plan_entry=False)
        got = b.project(
            record,
            None,
            recovered=kind == "interrupted",
            error=answer.execution_error(kind, row),
            terminal_kind=kind,
        )
        want = classify(kind, row, recovered=kind == "interrupted")
        assert got.outcome.value == want.outcome_class.value, kind
        assert got.listed == ()  # a plan-less root lists nothing
    # its primary at (): CANDIDATE for what ended by itself, STOPPED for a stop
    passed = b.project(b.folded(plan_entry=False), None)
    assert (passed.primary.listing, passed.primary.node_class) == (
        Listing.CANDIDATE,
        NodeClass.PASSED,
    )
    cancelled = b.project(b.folded(stops=(CANCEL,), plan_entry=False), None)
    assert cancelled.primary.listing is Listing.STOPPED and cancelled.root_stop is RootStop.CANCEL
    unencodable = b.project(
        b.folded(plan_entry=False),
        None,
        error=answer.execution_error(
            "failed", {"code": codes.EXECUTION_RESULT_UNENCODABLE, "phase": "r", "message": "m"}
        ),
    )
    assert unencodable.primary.node_class is NodeClass.UNENCODABLE_RESULT
    assert unencodable.error is not None and unencodable.error.code == vocab.RESULT_UNENCODABLE
    # a run written before stop rows existed answers by its terminal kind
    legacy = b.project(b.folded(plan_entry=False), None, terminal_kind="timed_out")
    assert legacy.outcome is OutcomeClass.TIMED_OUT and legacy.incomplete == ()


# -- rule (3): the key decides ------------------------------------------------------------------


def test_rule3_key_decides_outcome_b4_t3() -> None:
    plan = b.make_plan()
    root = b.end("", None)  # a composite that ended normally
    cases = [
        (b.end("n1", C.FAILED), b.satisfied("n2"), OutcomeClass.FAILED, NodeClass.FAILED),
        (
            b.end("n1", C.BLOCKED, P["PRECONDITION_UNSATISFIED"]),
            b.satisfied("n2"),
            OutcomeClass.BLOCKED,
            NodeClass.BLOCKED,
        ),
        (
            b.end("n1", C.BLOCKED, P["PRECONDITION_UNSATISFIED"]),
            b.end("n2", C.FAILED),
            OutcomeClass.FAILED,
            NodeClass.FAILED,
        ),
        (
            b.end("n1", C.FAILED, P["UNIT_RAISED"]),
            b.end("n2", C.FAILED),
            OutcomeClass.EXECUTION_ERROR,
            NodeClass.EXECUTION_ERROR,
        ),
        (
            b.end("n1", C.FAILED, P["EXECUTION_DEADLINE"]),
            b.satisfied("n2"),
            OutcomeClass.TIMED_OUT,
            NodeClass.TIMED_OUT,
        ),
    ]
    for one, two, outcome, klass in cases:
        got = b.project(b.folded(one, two, root, plan=plan), plan)
        assert got.outcome is outcome and got.primary.node_class is klass, (one, two)
        assert got.root_stop is None and got.recovered is False
    # every vertex satisfied: PASSED, and a repair anywhere outranks the root's PASSED
    repaired = b.ticket("n2", confirmation=b.APPLIED, remedy=lf.RemedyGrant("c", "up", 1))
    got = b.project(
        b.folded(b.satisfied("n1"), b.satisfied("n2"), root, entries=(repaired,), plan=plan), plan
    )
    assert got.outcome is OutcomeClass.PASSED and got.primary.path == ("n2",)
    assert got.primary.node_class is NodeClass.REPAIRED
    assert got.primary.disposition is ResourceDisposition.REPAIRED  # a repair is never clean


def test_rule3_exhausted_answered_blocked() -> None:
    plan = b.make_plan()
    exhausted = b.end(
        "n1",
        C.BLOCKED,
        P["POSTCONDITION_TIMEOUT"],
        human_action="wait for it",
        resend=lf.Resend.SUCCEEDS_AFTER_ACTION,
    )
    got = b.project(b.folded(exhausted, b.satisfied("n2"), b.end("", None), plan=plan), plan)
    assert got.outcome is OutcomeClass.BLOCKED  # EXHAUSTED_AS: never FAILED (B4-I2)
    assert got.primary.node_class is NodeClass.EXHAUSTED  # the tier is kept
    assert (got.primary.human_action, got.primary.resend) == (
        "wait for it",
        "succeeds_after_action",
    )
    assert answer.EXHAUSTED_AS is OutcomeClass.BLOCKED


# -- rule (4): the plugin ended before every vertex did ---------------------------------------


def test_rule4_not_ended_vertex_unended_error() -> None:
    plan = b.make_plan()
    entries = (b.ticket("n2", confirmation=b.APPLIED),)
    record = b.folded(b.satisfied("n1"), entries=entries, plan=plan, ended=False)
    got = b.project(record, plan)
    assert got.outcome is OutcomeClass.EXECUTION_ERROR and got.root_stop is None
    assert got.error is not None and got.error.code == vocab.VERTEX_UNENDED
    assert got.error.phase == "n2"  # the lowest-ordinal unended vertex
    by_path = {n.path: n.listing for n in (got.primary, *got.listed)}
    assert by_path[("n2",)] is Listing.UNENDED and by_path[()] is Listing.NOT_STARTED
    # no plan entry recorded: every vertex of the scope is NOT_STARTED
    none = b.project(b.folded(plan_entry=False, plan=plan, ended=False), plan)
    assert {n.listing for n in (none.primary, *none.listed)} == {Listing.NOT_STARTED}
    assert (
        none.error is not None
        and none.error.code == vocab.VERTEX_UNENDED
        and none.error.phase == "n1"  # nothing unended: the lowest-ordinal vertex
    )


def test_rule4_worker_exit_error_from_record() -> None:
    plan = b.make_plan()
    record = b.folded(b.end("n1", C.FAILED), plan=plan, ended=False)
    worker = _err(vocab.WORKER_EXIT, "exit", "the child exited with status 3")
    got = b.project(record, plan, error=worker)
    assert got.error == worker and got.outcome is OutcomeClass.EXECUTION_ERROR
    assert got.primary.path == ("n1",)  # the key-min candidate, the crash is not a vertex
    raised = b.project(record, plan, error=_err(vocab.UNIT_RAISED, "run", "raised"))
    assert raised.error is not None and raised.error.code == vocab.UNIT_RAISED


# -- the primary, the listing, the required fields ---------------------------------------------


def test_primary_defined_on_every_path_success_included() -> None:
    plan = b.make_plan()
    paths = {
        "rule1": b.project(b.folded(stops=(CANCEL,), plan=plan, ended=False), plan),
        "rule2": b.project(b.folded(plan=plan, ended=False), plan, recovered=True),
        "rule3-pass": b.project(
            b.folded(b.satisfied("n1"), b.satisfied("n2"), b.end("", None), plan=plan), plan
        ),
        "rule3-fail": b.project(
            b.folded(b.end("n1", C.FAILED), b.satisfied("n2"), b.end("", None), plan=plan), plan
        ),
        "rule4": b.project(b.folded(plan=plan, ended=False), plan),
        "rule5": b.project(b.folded(plan_entry=False), None),
    }
    assert all(a.primary is not None for a in paths.values())
    ok = paths["rule3-pass"]
    assert ok.outcome is OutcomeClass.PASSED
    # a PASSED run's primary is the root at () as ROLLED_UP PASSED
    assert (ok.primary.path, ok.primary.listing, ok.primary.node_class) == (
        (),
        Listing.ROLLED_UP,
        NodeClass.PASSED,
    )
    assert {n.path for n in ok.listed} == {("n1",), ("n2",)}  # every other vertex


def test_listed_group_order_and_unconfirmed_once_entries() -> None:
    plan = b.make_plan(("all", tuple(trees.LEAF for _ in range(6))))
    ends = (
        b.end("n1", C.BLOCKED, P["PRECONDITION_UNSATISFIED"]),  # candidate, rank 5
        b.end("n2", C.FAILED),  # candidate, rank 2: first by key
        b.end("n3", C.CONVERGING, cut=lf.Cut.STOPPED),  # stopped
        b.end("n5", None, cut=lf.Cut.NOT_STARTED),  # not started (cut)
    )
    entries = (
        b.ticket("n4", "b", confirmation=None),  # n4: entries and no end: UNENDED
        b.ticket("n4", "a", repeat=lf.Repeat.ONCE, confirmation=None),  # unconfirmed ONCE
        b.ticket("n1", "z", repeat=lf.Repeat.ONCE, confirmation=None, attempt=2),
        b.ticket("n1", "y", repeat=lf.Repeat.ONCE, confirmation=None),
        b.ticket("n2", "safe", repeat=lf.Repeat.SAFE, confirmation=None),  # SAFE: not listed
    )
    got = b.project(b.folded(*ends, entries=entries, plan=plan, ended=False), plan)
    order = [(n.path, n.listing.value) for n in (got.primary, *got.listed)]
    assert order == [
        (("n2",), "candidate"),  # the primary: the key-min (FAILED outranks BLOCKED)
        (("n1",), "candidate"),
        (("n3",), "stopped"),
        (("n4",), "unended"),
        ((), "not_started"),  # the root has neither entries nor a NodeEnd; ordinal 1
        (("n5",), "not_started"),  # each group by precedence ordinal
        (("n6",), "not_started"),
    ]
    # every ONCE entry of the unconfirmed set, by vertex ordinal, effect, attempt; SAFE ones never
    assert [(u.path, u.effect, u.attempt) for u in got.unconfirmed] == [
        (("n1",), "y", 1),
        (("n1",), "z", 2),
        (("n4",), "a", 1),
    ]


def test_required_fields_per_class_b4_c5() -> None:
    plan = b.make_plan()
    root = b.end("", None)
    counts = lf.TestCounts(passed=3, failed=1, errors=2, skipped=0)
    failing = b.ticket(
        "n1",
        result=lf.RecordedResult(False, "tests.failed", counts),
        confirmation=b.APPLIED,
    )
    failed = b.project(
        b.folded(
            b.end("n1", C.FAILED, "tests.failed"),
            b.satisfied("n2"),
            root,
            entries=(failing,),
            plan=plan,
        ),
        plan,
    )
    assert failed.outcome is OutcomeClass.FAILED
    assert failed.primary.code == "tests.failed" and failed.primary.path == ("n1",)
    assert (
        failed.test_counts == answer.TestCounts(3, 1, 2, 0)
        and failed.detail == "run-root-0001/answer"
    )
    blocked = b.project(
        b.folded(
            b.end(
                "n1",
                C.BLOCKED,
                P["PRECONDITION_UNSATISFIED"],
                human_action="log in",
                resend=lf.Resend.SUCCEEDS_AFTER_ACTION,
            ),
            b.satisfied("n2"),
            root,
            plan=plan,
        ),
        plan,
    )
    assert (blocked.primary.human_action, blocked.primary.resend) == (
        "log in",
        "succeeds_after_action",
    )
    cancelled = b.project(b.folded(stops=(CANCEL,), plan=plan, ended=False), plan)
    assert cancelled.cleanup is not None and cancelled.outcome is OutcomeClass.CANCELLED
    timed = b.project(b.folded(stops=(RELEASE,), plan=plan, ended=False), plan)
    assert timed.incomplete is not None and timed.error is None
    errored = b.project(
        b.folded(b.end("n1", C.FAILED, P["UNIT_RAISED"]), b.satisfied("n2"), root, plan=plan), plan
    )
    assert errored.error is not None and errored.error.code == vocab.UNIT_RAISED
    assert errored.error.phase == "n1" and errored.incomplete is None
    passed = b.project(
        b.folded(
            b.satisfied("n1", provenance=lf.Provenance.FOUND), b.satisfied("n2"), root, plan=plan
        ),
        plan,
    )
    assert passed.outcome is OutcomeClass.PASSED
    dispositions = {n.path: n.disposition for n in passed.listed}
    assert dispositions[("n1",)] is ResourceDisposition.REUSED
    assert dispositions[("n2",)] is ResourceDisposition.STARTED
    # `error` and `incomplete` are exclusive
    for got in (failed, blocked, cancelled, timed, errored, passed):
        assert not (got.error is not None and got.incomplete is not None)


# -- the bound and the carrier -------------------------------------------------------------------


def test_decisive_fields_never_truncated_rest_behind_detail() -> None:
    plan = b.make_many(100)
    ends = [
        b.end(f"n{i}", C.BLOCKED, P["PRECONDITION_UNSATISFIED"], human_action="act " * 20)
        for i in range(1, 101)
    ]
    record = b.folded(*ends, b.end("", None), plan=plan)
    full = b.project(record, plan, budget=10**9)
    tight = b.project(record, plan)
    assert full.detail is None and tight.detail == "run-root-0001/answer"  # overflow sets detail
    wire = answer.to_wire(tight, 4096)
    assert len(json.dumps(wire, separators=(",", ":")).encode()) <= 4096
    assert wire["listed_count"] == 100 and 0 < len(wire["listed"]) < 100  # counts always inline
    # the inline listed is a prefix of the full order; the decisive fields are the full ones
    full_wire = answer.to_wire_full(tight)
    assert wire["listed"] == full_wire["listed"][: len(wire["listed"])]
    for key in (
        "outcome",
        "root_stop",
        "recovered",
        "primary",
        "cleanup",
        "error",
        "incomplete",
        "test_counts",
        "detail",
    ):
        assert wire[key] == full_wire[key], key
    assert len(full_wire["listed"]) == 100
    # V-13's arithmetic: the maximal decisive fields fit the default budget
    assert bounds.PRIMARY_MAX == 3968 <= bounds.SUMMARY_BUDGET_DEFAULT
    worst = b.end(
        "n1",
        C.BLOCKED,
        P["PRECONDITION_UNSATISFIED"],
        human_action="h" * bounds.HUMAN_ACTION_MAX,
        resend=lf.Resend.WILL_NOT_SUCCEED,
    )
    big = b.project(
        b.folded(worst, *(b.satisfied(f"n{i}") for i in range(2, 101)), b.end("", None), plan=plan),
        plan,
    )
    decisive = answer.to_wire(big, bounds.PRIMARY_MAX)
    assert decisive["primary"]["human_action"] == "h" * bounds.HUMAN_ACTION_MAX  # never cut


def test_cleanup_composition_b4_c7() -> None:
    plan = b.make_plan()
    released = b.ticket("n1", "loop", released=True)
    other = b.ticket("n2", "sweptup")
    record = b.folded(
        b.satisfied("n1"), b.satisfied("n2"), b.end("", None), entries=(released, other), plan=plan
    )
    target = sweep.SweepTarget(("n2",), "sweptup")
    both = CleanupDisposition(released=(target, sweep.GROUP_TARGET), unknown=(target,))
    got = b.project(record, plan, cleanup=both)
    # the loop's recorded release is one released target; `unknown` dominates the target the sweep
    # both released and left unknown; the group target is released
    assert (got.cleanup.released, got.cleanup.unknown, got.cleanup.clean) == (2, 1, False)
    clean = b.project(
        record, plan, cleanup=CleanupDisposition(released=(target, sweep.GROUP_TARGET))
    )
    assert clean.cleanup.clean and clean.cleanup.released == 3
    # never clean without confirmation: an overflowed lane, a refused entry, an unconfirmed group
    assert not b.project(b.folded(overflowed=True, plan_entry=False), None).cleanup.clean
    assert not b.project(b.folded(unknown_paths=("x",), plan_entry=False), None).cleanup.clean
    live = b.project(
        b.folded(plan_entry=False),
        None,
        group=b.LiveGroup(),
        cleanup=CleanupDisposition(unknown=(sweep.GROUP_TARGET,)),
    )
    assert (live.cleanup.group_confirmed_gone, live.cleanup.clean) == (False, False)
    helpers = b.ticket("g", release=lf.InRunGroup(helpers_disclosed=True), confirmation=b.APPLIED)
    assert b.project(b.folded(entries=(helpers,), plan_entry=False), None).cleanup.helpers_disclosed
    # a lease ended while the group was not confirmed gone (OQ-34)
    leased = AdmittedPlan.from_json(json.dumps(json.loads(plan.to_json()))) if False else plan
    assert not b.project(record, leased, group=b.LiveGroup()).cleanup.lease_ended_unconfirmed


def test_empty_fold_queued_run_rule1() -> None:
    """A run finalized while queued: the empty fold B2-C12 states, one stop row, decided by
    rule (1) and never as 'lane unreadable'; nothing was created, the group is confirmed gone."""
    for cause, outcome in ((CANCEL, OutcomeClass.CANCELLED), (RELEASE, OutcomeClass.TIMED_OUT)):
        record = b.folded(stops=(cause,), plan_entry=False)
        got = b.project(record, None, cleanup=CleanupDisposition())
        assert got.outcome is outcome and got.error is None
        assert got.cleanup == answer.CleanupAnswer(True, 0, 0, 0, 0, True, False, False)
        assert got.listed == () and got.unconfirmed == ()


# -- F-11(a): LANE_UNAVAILABLE's class (provisional BLOCKED) --------------------------------------


@pytest.mark.gated_on("F-11(a)")
@pytest.mark.parametrize(
    "variant",
    [
        "blocked",
        pytest.param(
            "failed",
            marks=[
                pytest.mark.gated_on("F-11(a)"),
                pytest.mark.xfail(strict=True, reason="variant:F-11(a)=failed"),
            ],
        ),
    ],
)
def test_lane_unavailable_class_variant(variant: str) -> None:
    plan = b.make_plan()
    lane_full = b.end("n1", C.BLOCKED, P["LANE_UNAVAILABLE"], human_action="the lane is full")
    got = b.project(b.folded(lane_full, b.satisfied("n2"), b.end("", None), plan=plan), plan)
    expected = {"blocked": OutcomeClass.BLOCKED, "failed": OutcomeClass.FAILED}[variant]
    assert got.outcome is expected
    assert prec.node_class(lane_full) is NodeClass.BLOCKED


# -- the codes ---------------------------------------------------------------------------------


def test_codes_reexport_additive() -> None:
    core = {
        codes.EXECUTION_IMPORT_FAILED, codes.EXECUTION_BIND_FAILED, codes.EXECUTION_PLUGIN_RAISED,
        codes.EXECUTION_RESULT_UNENCODABLE, codes.EXECUTION_PROVENANCE_MISMATCH,
        codes.EXECUTION_CANCELLED, codes.EXECUTION_DEADLINE_EXCEEDED, codes.EXECUTION_WORKER_EXIT,
        codes.EXECUTION_INTERRUPTED,
    }  # fmt: skip
    assert core <= codes.EXECUTION_CODES  # every core member still present
    for name in vocab.SINGLE_LEVEL_CODES:
        assert name in {getattr(codes, n) for n in dir(codes) if n.isupper()}, name
    assert {
        c for c in vocab.SINGLE_LEVEL_CODES if c.startswith("execution.")
    } <= codes.EXECUTION_CODES
    assert codes.UNIT_RAISED == vocab.UNIT_RAISED and codes.VERTEX_UNENDED == vocab.VERTEX_UNENDED
    # the V-11 codes core already spells have one value
    assert vocab.WORKER_EXIT == codes.EXECUTION_WORKER_EXIT
    assert vocab.EXECUTION_RESTART == codes.EXECUTION_INTERRUPTED
    assert vocab.BUDGET_DOES_NOT_FIT == codes.BUDGET_DOES_NOT_FIT


# -- durable inputs: restart, crash, and the run view ---------------------------------------------


def _plan_run(kernel: object, run_id: str, *entries: lf.Entry, human_action: str = "") -> Path:
    """A started run with a spec carrying a compiled one-leaf plan and a lane holding `entries`
    (the plan entry first): the durable inputs of a plan-bearing run whose server died."""
    home = kernel.home  # type: ignore[attr-defined]
    run_dir = seed_interrupted_run(home, run_id, last_kind="started")
    declared = trees.tree("root", {"": trees.leaf_node("root")})
    plan = compile(declared, {})
    assert isinstance(plan, AdmittedPlan)
    plan = carving.attach(plan, {}, 0.0)
    atomic_write_json(
        evidence_dir(run_dir) / "spec.json",
        {"plugin": "echo", "snapshot_id": "snap_test", "plan": json.loads(plan.to_json())},
    )
    lines = [lf.encode_entry(e, i + 1) + b"\n" for i, e in enumerate(entries)]
    lf.lane_path(run_dir).write_bytes(b"".join(lines))
    return run_dir


def _root_end(condition: C, code: str | None, human_action: str | None) -> lf.NodeEnd:
    return lf.NodeEnd(
        lineage=lf.Lineage("r", ()),
        at=b.T0,
        condition=condition,
        code=code,
        human_action=human_action,
        resend=lf.Resend.SUCCEEDS_AFTER_ACTION if human_action else None,
        provenance=None,
        cut=None,
    )


def _answer_of(kernel: object, run_id: str) -> tuple[RunView, dict[str, object]]:
    view = kernel.control.project.status(run_id)  # type: ignore[attr-defined]
    assert isinstance(view, RunView) and view.answer is not None
    return view, view.answer


def test_primary_human_action_byte_identical_to_node_end_across_restart(tmp_path: Path) -> None:
    kernel = support.spine_kernel(tmp_path / "home")
    action = "h" * bounds.HUMAN_ACTION_MAX
    plan_entry = lf.PlanEntry(lf.PlanIdentity("d" * 8, "a" * 8, {}, "o" * 8))
    end = _root_end(C.BLOCKED, P["PRECONDITION_UNSATISFIED"], action)
    run_dir = _plan_run(kernel, "r_big_action", plan_entry, end)
    recover_run_dir(run_dir)  # the server died before the terminal row; recovery finalizes
    _, before = _answer_of(kernel, run_dir.name)
    assert before["primary"]["human_action"] == action  # type: ignore[index]
    assert len(json.dumps(before, separators=(",", ":")).encode()) <= bounds.SUMMARY_BUDGET_DEFAULT
    # a restarted server recomputes the same answer from the same durable inputs
    restarted = create_kernel(home=kernel.home, plugin_dirs=[], skip_recovery=False)
    _, after = _answer_of(restarted, run_dir.name)
    assert after == before and after["primary"]["human_action"] == action  # type: ignore[index]
    persisted = json.loads((evidence_dir(run_dir) / "answer.json").read_text(encoding="utf-8"))
    assert persisted["primary"]["human_action"] == action  # the finalized answer, byte-identical


def test_crash_after_every_node_end_answer_and_runview_error_diverge(tmp_path: Path) -> None:
    kernel = support.spine_kernel(tmp_path / "home")
    plan_entry = lf.PlanEntry(lf.PlanIdentity("d" * 8, "a" * 8, {}, "o" * 8))
    run_dir = _plan_run(
        kernel, "r_crash_after_ends", plan_entry, _root_end(C.SATISFIED, None, None)
    )
    recover_run_dir(run_dir)
    view, ans = _answer_of(kernel, run_dir.name)
    # the answer follows the key (every NodeEnd was recorded): passed, recovered
    assert (ans["outcome"], ans["recovered"], ans["root_stop"]) == ("passed", True, None)
    # while RunView.error still shows the crash's record (B4-C2's confirmed consequence, MQ-6)
    assert view.error is not None and view.error["code"] == codes.EXECUTION_INTERRUPTED
    assert view.outcome is not None and view.outcome["class"] == "execution_error"


def test_run_view_carries_answer_only_when_terminal(tmp_path: Path) -> None:
    kernel = support.spine_kernel(tmp_path / "home")
    live = seed_interrupted_run(kernel.home, "r_still_running", last_kind="started")
    running = kernel.control.project.status(live.name)
    assert isinstance(running, RunView) and running.state == "running"
    assert running.answer is None and "answer" not in running.to_dict()  # WR-TERM-2
    done = kernel.control.run(
        plugin="echo", args={"message": "hi"}, wait_ms=support.SHORT_DEADLINE_S * 1000
    )
    assert isinstance(done, RunView) and done.state == "succeeded"
    wire = done.to_dict()
    assert wire["answer"]["outcome"] == "passed" and wire["outcome"]["class"] == "passed"
    # every pre-existing key is unchanged beside it
    assert {"run_id", "state", "status_frame_version", "outcome", "cleanup"} <= set(wire)
    assert isinstance(kernel.control.project.fetch(f"{done.run_id}/answer", {"kind": "head"}), dict)


def test_ledger_untouched_by_answer_projection(tmp_path: Path) -> None:
    """The answer is a pure projection: reading it writes nothing to the ledger."""
    kernel = support.spine_kernel(tmp_path / "home")
    done = kernel.control.run(
        plugin="echo", args={"message": "hi"}, wait_ms=support.SHORT_DEADLINE_S * 1000
    )
    assert isinstance(done, RunView)
    path = ledger_path(support.run_dir_of(kernel, done.run_id))
    before = RunLedger.open(path).records
    kernel.control.project.status(done.run_id)
    assert RunLedger.open(path).records == before


def test_mcp_run_and_await_runs_terminal_responses_carry_answer() -> None:
    """The carrier (B4-C6): the MCP `run` and `await_runs` terminal responses carry `answer` beside
    today's run state; a non-terminal frame carries none; no tool was added."""
    from tests.proof import mcp_host, tolerances

    with mcp_host.McpHost() as host:
        started = host.call("run", {"plugin": "slow", "args": {"seconds": 30}, "wait_ms": 0})
        assert "answer" not in started  # not terminal: no class yet (WR-TERM-2)
        host.call("cancel", {"run_id": started["run_id"]})
        joined = host.call(
            "await_runs",
            {
                "run_ids": [started["run_id"]],
                "mode": "all",
                "timeout_ms": tolerances.HARNESS_WAIT_MS,
            },
        )
        views = joined["result"] if isinstance(joined, dict) and "result" in joined else joined
        (view,) = views
        assert view["state"] == "cancelled" and view["outcome"]["class"] == "cancelled"
        assert view["answer"]["outcome"] == "cancelled" and view["answer"]["root_stop"] == "cancel"
        done = host.call(
            "run",
            {"plugin": "echo", "args": {"message": "hi"}, "completion": "terminal"},
        )
        assert done["state"] == "succeeded" and done["answer"]["outcome"] == "passed"
        assert done["answer"]["primary"]["path"] == []
