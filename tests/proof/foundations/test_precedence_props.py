"""Property suite for precedence and node class (MC-22; L.SV-4.1): shape evidence only."""

from __future__ import annotations

import ast
import random
from dataclasses import dataclass
from pathlib import Path

from tests.proof.foundations import trees
from trestle.common.outcome import OutcomeClass
from trestle.common.plan import precedence
from trestle.common.plan import vocabulary as vocab
from trestle.common.plan.compiler import AdmittedPlan, compile
from trestle.common.plan.precedence import Candidate, key, primary, roll_up
from trestle.common.plan.vocabulary import Listing, NodeClass, Origin, ResourceDisposition

ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class End:
    condition: str | None
    code: str | None = None
    cut: str | None = None
    provenance: str | None = None


@dataclass(frozen=True)
class Conf:
    status: str


@dataclass(frozen=True)
class Ticket:
    confirmation: Conf | None
    remedy: object | None = None


REPAIR = (Ticket(Conf("applied"), remedy="grant"),)
V = vocab.V11


def _class(condition: str | None, code: str | None = None, **kw: object) -> NodeClass | None:
    tickets = kw.pop("tickets", ())
    return precedence.node_class(End(condition, code, **kw), tickets)  # type: ignore[arg-type]


def test_node_class_first_matching_row_b4_t2() -> None:
    C = NodeClass
    # row 1: a cut end, or a composite with no condition of its own, is not a candidate
    assert _class("failed", cut="stopped") is None
    assert _class(None, cut="not_started") is None
    assert _class(None) is None
    assert _class("satisfied", None, cut="stopped") is None
    # rows 2-3
    assert _class("failed", V["UNIT_RAISED"]) is C.EXECUTION_ERROR
    assert _class("blocked", V["DECLARATION_STALE"]) is C.EXECUTION_ERROR
    # rows 5-6: satisfied is passed, repaired with a confirmed applied ticket carrying a remedy
    assert _class("satisfied") is C.PASSED
    assert _class("satisfied", tickets=REPAIR) is C.REPAIRED
    assert _class("satisfied", tickets=(Ticket(Conf("unknown"), "grant"),)) is C.PASSED
    assert _class("satisfied", tickets=(Ticket(Conf("applied"), None),)) is C.PASSED
    assert _class("satisfied", tickets=(Ticket(None, "grant"),)) is C.PASSED
    # an earlier row wins
    assert _class("satisfied", V["UNIT_RAISED"]) is C.EXECUTION_ERROR
    # row 7
    for name in ("CARVE_EXCEEDED", "EXECUTION_DEADLINE"):
        assert _class("failed", V[name]) is C.TIMED_OUT
        assert _class("unsatisfied", V[name]) is C.TIMED_OUT
    # row 8 beats rows 9-11 whatever the condition
    for name in ("POSTCONDITION_TIMEOUT", "EFFECT_UNCONFIRMED", "CURRENCY_UNCONFIRMED"):
        for condition in ("blocked", "failed", "in_doubt", "stale"):
            assert _class(condition, V[name]) is C.EXHAUSTED, (name, condition)
    assert _class("blocked", "execution.credential_lifetime_insufficient") is C.EXHAUSTED
    # rows 9-10
    assert _class("blocked", None) is C.BLOCKED
    assert _class("blocked", "some.unit_code") is C.BLOCKED
    for name in ("PRECONDITION_UNSATISFIED", "ROUTE_UNSUPPORTED", "REMEDY_EXHAUSTED"):
        assert _class("unsatisfied", V[name]) is C.BLOCKED, name
    for name in ("REMEDY_NO_PROGRESS", "LANE_UNAVAILABLE", "BUDGET_DOES_NOT_FIT"):
        assert _class("blocked", V[name]) is C.BLOCKED, name
    assert _class("blocked", "execution.section_unavailable") is C.BLOCKED
    assert _class("incompatible", None) is C.BLOCKED
    for name in ("FOUND_INCOMPATIBLE", "FOUND_UNHEALTHY"):
        assert _class("failed", V[name]) is C.BLOCKED, name
    assert _class("stale", "execution.credential_stale") is C.BLOCKED
    # row 11: failed, and any other code
    assert _class("failed", None) is C.FAILED
    assert _class("failed", "unit.its_own_code") is C.FAILED
    assert _class("failed", V["EXECUTION_CANCELLED"]) is C.FAILED
    for condition in ("unsatisfied", "converging", "stale", "in_doubt"):
        assert _class(condition, "unit.other") is C.FAILED, condition
    # never dropped: every condition and code lands in some class or is a non-candidate
    for condition in (
        "satisfied",
        "unsatisfied",
        "converging",
        "stale",
        "in_doubt",
        "incompatible",
    ):
        assert _class(condition, "x.y") is not None


def test_resource_disposition_rule_b4_c3() -> None:
    R = ResourceDisposition
    assert precedence.resource_disposition(End("satisfied", provenance="created"), ()) is R.STARTED
    assert precedence.resource_disposition(End("satisfied", provenance="found"), ()) is R.REUSED
    assert (
        precedence.resource_disposition(End("satisfied", provenance="created"), REPAIR)
        is R.REPAIRED
    )
    assert (
        precedence.resource_disposition(End("satisfied", provenance="found"), REPAIR) is R.REPAIRED
    )
    unconfirmed = (Ticket(Conf("unknown"), "grant"),)
    assert (
        precedence.resource_disposition(End("satisfied", provenance="created"), unconfirmed)
        is R.STARTED
    )
    assert precedence.resource_disposition(End("failed", provenance="created"), ()) is None
    assert precedence.resource_disposition(End("blocked", provenance="found"), REPAIR) is None
    assert precedence.resource_disposition(End(None, cut="stopped"), ()) is None
    assert precedence.resource_disposition(End("satisfied", provenance=None), ()) is None


def _cand(name: str, klass: NodeClass, ordinal: int, code: str | None = None) -> Candidate:
    return Candidate(name, klass, code, ordinal)


def test_key_class_then_origin_then_ordinal() -> None:
    classes = list(NodeClass)
    assert [precedence.RANK[c] for c in classes] == list(range(len(classes)))
    every = [
        _cand(f"{c.value}{o}", c, o, code)
        for c in classes
        for o in (0, 1, 7)
        for code in (None, V["UNIT_RAISED"])
    ]
    for a in every:
        for b in every:
            expected = (
                classes.index(a.node_class),
                int(precedence.origin_of(a.code)),
                a.ordinal,
            ) < (classes.index(b.node_class), int(precedence.origin_of(b.code)), b.ordinal)
            assert (key(a) < key(b)) is expected
    # class outranks origin outranks ordinal
    assert key(_cand("a", NodeClass.FAILED, 9)) < key(_cand("b", NodeClass.BLOCKED, 0))
    trigger = _cand("t", NodeClass.EXECUTION_ERROR, 9, V["UNIT_RAISED"])
    other = _cand("o", NodeClass.EXECUTION_ERROR, 0, V["DECLARATION_STALE"])
    assert key(trigger) < key(other)
    assert key(_cand("x", NodeClass.FAILED, 1)) < key(_cand("y", NodeClass.FAILED, 2))


def test_unit_raised_origin_zero_outranks_every_class() -> None:
    trigger = _cand("t", NodeClass.EXECUTION_ERROR, 99, V["UNIT_RAISED"])
    assert precedence.origin_of(V["UNIT_RAISED"]) is Origin.WHOLE_ROOT_TRIGGER
    assert precedence.origin_of(V["DECLARATION_STALE"]) is Origin.NODE_REACHED
    assert precedence.origin_of(None) is Origin.NODE_REACHED
    for klass in NodeClass:
        assert key(trigger) <= key(_cand("c", klass, 0)), klass
    assert primary([_cand("c", k, 0) for k in NodeClass] + [trigger]) == trigger
    # node_class puts an UNIT_RAISED end in the only class origin 0 can occupy
    assert _class("failed", V["UNIT_RAISED"]) is NodeClass.EXECUTION_ERROR


def test_candidacy_excludes_cut_rolled_up_unended_not_started() -> None:
    L = Listing
    assert precedence.listing(End("failed", cut="stopped"), True) is L.STOPPED
    assert precedence.listing(End(None, cut="not_started"), False) is L.NOT_STARTED
    assert precedence.listing(End(None), True) is L.ROLLED_UP
    assert precedence.listing(None, True) is L.UNENDED
    assert precedence.listing(None, False) is L.NOT_STARTED
    assert precedence.listing(End("blocked"), True) is L.CANDIDATE
    assert precedence.listing(End("satisfied"), True) is L.CANDIDATE
    for end in (End("failed", cut="stopped"), End(None, cut="not_started"), End(None)):
        assert precedence.node_class(end) is None
        assert precedence.listing(end, True) is not L.CANDIDATE
    # a vertex is a candidate iff node_class returns a class for it as CANDIDATE
    for condition in ("satisfied", "blocked", "failed", "unsatisfied"):
        end = End(condition)
        assert (precedence.listing(end, True) is L.CANDIDATE) == (
            precedence.node_class(end) is not None
        )


def _assigned(plan: AdmittedPlan, rng: random.Random) -> list[Candidate]:
    classes = [None, *NodeClass]
    out = []
    for path in plan.paths:
        klass = rng.choice(classes)
        if klass is not None:
            code = (
                V["UNIT_RAISED"]
                if klass is NodeClass.EXECUTION_ERROR and rng.random() < 0.3
                else None
            )
            out.append(Candidate(path, klass, code, plan.precedence_ordinal[path]))
    return out


def test_primary_is_key_min_independent_of_candidate_order() -> None:
    checked = 0
    for label, declared in trees.all_trees():
        plan = compile(declared, {})
        if not isinstance(plan, AdmittedPlan):
            continue
        rng = random.Random(label)
        for _ in range(4):
            candidates = _assigned(plan, rng)
            if not candidates:
                assert primary(candidates) is None
                continue
            expected = min(
                candidates,
                key=lambda c: (
                    precedence.RANK[c.node_class],
                    int(precedence.origin_of(c.code)),
                    c.ordinal,
                ),
            )
            shuffled = list(candidates)
            rng.shuffle(shuffled)
            assert primary(shuffled) == primary(candidates) == expected
            # depth does not matter: two candidates differ only in ordinal, the lower ordinal wins
            checked += 1
    assert checked > 300


def test_wrapping_keeps_relative_order() -> None:
    """B4-C4's property: wrapping a node in a composite that keeps its declaration position and
    edges names the same logical node as primary (design S-8, F-9)."""
    checked = 0
    for label, declared in trees.all_trees():
        if "share" in label:
            continue
        plan = compile(declared, {})
        if not isinstance(plan, AdmittedPlan):
            continue
        for target in plan.paths:
            wrapped = trees.wrap_node(declared, target)
            if wrapped is None:
                continue
            new_tree, mapping = wrapped
            new_plan = compile(new_tree, {})
            assert isinstance(new_plan, AdmittedPlan)
            rng = random.Random(f"{label}{target}")
            for _ in range(3):
                original = _assigned(plan, rng)
                moved = [
                    Candidate(
                        mapping[c.path],
                        c.node_class,
                        c.code,
                        new_plan.precedence_ordinal[mapping[c.path]],
                    )
                    for c in original
                ]
                before, after = primary(original), primary(moved)
                assert (before is None) == (after is None)
                if before is not None and after is not None:
                    assert after.path == mapping[before.path], (label, target)
                    checked += 1
    assert checked > 300


def test_roll_up_is_subtree_key_min_b4_c8() -> None:
    cands = [
        _cand("a", NodeClass.BLOCKED, 0),
        _cand("b", NodeClass.FAILED, 3),
        _cand("c", NodeClass.PASSED, 1),
    ]
    assert roll_up(cands, True) is NodeClass.FAILED
    assert roll_up(cands, False) is NodeClass.FAILED  # candidates decide; satisfaction is moot
    assert roll_up(cands[:1], True) is NodeClass.BLOCKED
    assert roll_up([_cand("p", NodeClass.PASSED, 0)], True) is NodeClass.PASSED
    assert (
        roll_up([_cand("r", NodeClass.REPAIRED, 0), _cand("p", NodeClass.PASSED, 1)], True)
        is NodeClass.REPAIRED
    )
    # the same key as the root's: permuting the subtree changes nothing
    for _ in range(10):
        shuffled = list(cands)
        random.shuffle(shuffled)
        assert roll_up(shuffled, True) is NodeClass.FAILED
    trigger = _cand("t", NodeClass.EXECUTION_ERROR, 9, V["UNIT_RAISED"])
    assert roll_up([*cands, trigger], True) is NodeClass.EXECUTION_ERROR


def test_outcome_map_b4_t3_exhausted_as_blocked() -> None:
    Out = OutcomeClass
    assert precedence.EXHAUSTED_AS is Out.BLOCKED
    expected = {
        NodeClass.EXECUTION_ERROR: Out.EXECUTION_ERROR,
        NodeClass.UNENCODABLE_RESULT: Out.EXECUTION_ERROR,
        NodeClass.FAILED: Out.FAILED,
        NodeClass.TIMED_OUT: Out.TIMED_OUT,
        NodeClass.EXHAUSTED: Out.BLOCKED,
        NodeClass.BLOCKED: Out.BLOCKED,
        NodeClass.REPAIRED: Out.PASSED,
        NodeClass.PASSED: Out.PASSED,
    }
    for klass in NodeClass:
        assert precedence.outcome_of(klass) is expected[klass], klass
    # B4-I2: no class maps to FAILED except FAILED; the six-class answer set stays closed
    assert [k for k in NodeClass if precedence.outcome_of(k) is Out.FAILED] == [NodeClass.FAILED]
    assert {precedence.outcome_of(k) for k in NodeClass} <= set(Out)
    assert len(Out) == 6


def test_no_candidate_never_passed_unless_all_satisfied() -> None:
    assert primary([]) is None
    assert roll_up([], True) is NodeClass.PASSED
    assert roll_up([], False) is NodeClass.EXECUTION_ERROR
    assert precedence.outcome_of(roll_up([], False)) is OutcomeClass.EXECUTION_ERROR
    # with any candidate below PASSED the outcome is never PASSED
    for klass in NodeClass:
        outcome = precedence.outcome_of(roll_up([_cand("x", klass, 0)], True))
        assert (outcome is OutcomeClass.PASSED) == (klass in (NodeClass.PASSED, NodeClass.REPAIRED))
        mixed = roll_up([_cand("x", klass, 0), _cand("ok", NodeClass.PASSED, 1)], True)
        assert (precedence.outcome_of(mixed) is OutcomeClass.PASSED) == (
            klass in (NodeClass.PASSED, NodeClass.REPAIRED)
        )


def test_no_workflow_module_imports_precedence() -> None:
    """B1-C9, B1-O7: `trestle.workflow` never imports precedence and computes no primary."""
    offenders = []
    for path in sorted((ROOT / "trestle" / "workflow").rglob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                names = [base, *(f"{base}.{a.name}" for a in node.names)]
            if any(
                n.endswith("plan.precedence") or n == "trestle.common.plan.precedence"
                for n in names
            ):
                offenders.append(str(path))
    assert offenders == []


def test_precedence_imports_only_stdlib_plan_and_outcome() -> None:
    tree = ast.parse((ROOT / "trestle/common/plan/precedence.py").read_text())
    allowed = ("trestle.common.plan", "trestle.common.outcome")
    stdlib = {"__future__", "collections", "dataclasses", "typing", "enum"}
    for node in ast.walk(tree):
        modules = []
        if isinstance(node, ast.Import):
            modules = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules = [node.module]
        for module in modules:
            assert module.split(".")[0] in stdlib or module.startswith(allowed), module
