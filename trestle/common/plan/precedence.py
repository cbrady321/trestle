"""Precedence and node class (MC-22; L.SV-4.1): a transcription of B4's tables, inventing none.

* `node_class(end, tickets)` is B4-T2, first matching row, walked from `NODE_CLASS_ROWS` (data, so
  the SA-03 drift test compares the rows to their transcription). It returns `None` for a vertex
  that is not a candidate. B4-C3's `ResourceDisposition` rule is `resource_disposition`.
* `key(candidate)` is B4-C4's `(class rank, origin rank, precedence ordinal)` over B4-T1: the class
  rank is the `NodeClass` declaration order, the origin rank is `WHOLE_ROOT_TRIGGER` iff the code is
  `UNIT_RAISED`, and the ordinal is `AdmittedPlan.precedence_ordinal` (L.SV-3.3). `primary` is the
  key-min; `roll_up` is B4-C8's composite class.
* `outcome_of` is B4-T3 with `EXHAUSTED_AS = BLOCKED` (OQ-32, answered 2026-09-28: one variant, no
  switch value, B4-I2).

Root stops are never candidates: B4-C2 rules (1)-(2) decide them outside the key, in the host
(L.SV-4.2). This module is called only by the host answer (B4-I4); `trestle.workflow` never imports
it and computes no primary (B1-C9, B1-O7). `end` and `tickets` are read through structural
protocols of V-4.8 `NodeEnd` and V-4 `TicketEntry`, so this pure module imports no lane codec.

Imports the standard library, `trestle.common.plan` and `trestle.common.outcome` only.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol, final

from trestle.common.outcome import OutcomeClass
from trestle.common.plan import vocabulary as vocab
from trestle.common.plan.vocabulary import (
    Listing,
    NodeClass,
    Origin,
    ResourceDisposition,
)

EXHAUSTED_AS: OutcomeClass = OutcomeClass.BLOCKED  # OQ-32; the only switch, one variant


class _Confirmation(Protocol):
    @property
    def status(self) -> str: ...


class TicketLike(Protocol):
    """V-4 `TicketEntry`, the fields B4 reads."""

    @property
    def confirmation(self) -> _Confirmation | None: ...

    @property
    def remedy(self) -> object | None: ...


class NodeEndLike(Protocol):
    """V-4.8 `NodeEnd`, the fields B4 reads."""

    @property
    def condition(self) -> str | None: ...

    @property
    def code(self) -> str | None: ...

    @property
    def cut(self) -> str | None: ...

    @property
    def provenance(self) -> str | None: ...


# ---- B4-T1

#: class rank = position; origin: rank 0 iff the code is UNIT_RAISED
RANK: dict[NodeClass, int] = {cls: rank for rank, cls in enumerate(NodeClass)}


def _v11_name(code: str) -> str:
    """The V-11 name of a code: through `V11` for the spellings A-1 defines, else the upper-cased
    snake after the origin (so a later band's codes match their table row by name)."""
    for name, spelling in vocab.V11.items():
        if spelling == code:
            return name
    return code.rsplit(".", 1)[-1].upper()


# ---- B4-T2


@final
@dataclass(frozen=True, slots=True)
class Row:
    """One row of B4-T2. A row matches an end when any of its clauses does: `cut` (the end has a
    cut), `condition_none` (a composite with no loop-determined condition), one of `codes` (V-11
    names), or one of `conditions`; with `remedy` the row also needs a confirmed APPLIED ticket
    whose remedy is set; `catch_all` matches everything not matched above."""

    number: int
    klass: NodeClass | None
    cut: bool = False
    condition_none: bool = False
    codes: frozenset[str] = frozenset()
    conditions: frozenset[str] = frozenset()
    remedy: bool = False
    catch_all: bool = False
    unreachable: bool = False  # kept for id stability (row 4)


NODE_CLASS_ROWS: tuple[Row, ...] = (
    Row(1, None, cut=True, condition_none=True),
    Row(2, NodeClass.EXECUTION_ERROR, codes=frozenset({"UNIT_RAISED"})),
    Row(3, NodeClass.EXECUTION_ERROR, codes=frozenset({"DECLARATION_STALE"})),
    Row(4, NodeClass.UNENCODABLE_RESULT, codes=frozenset({"RESULT_UNENCODABLE"}), unreachable=True),
    Row(5, NodeClass.REPAIRED, conditions=frozenset({"satisfied"}), remedy=True),
    Row(6, NodeClass.PASSED, conditions=frozenset({"satisfied"})),
    Row(7, NodeClass.TIMED_OUT, codes=frozenset({"CARVE_EXCEEDED", "EXECUTION_DEADLINE"})),
    Row(
        8,
        NodeClass.EXHAUSTED,
        codes=frozenset(
            {
                "POSTCONDITION_TIMEOUT",
                "EFFECT_UNCONFIRMED",
                "CURRENCY_UNCONFIRMED",
                "CREDENTIAL_LIFETIME_INSUFFICIENT",
            }
        ),
    ),
    Row(
        9,
        NodeClass.BLOCKED,
        codes=frozenset(
            {
                "PRECONDITION_UNSATISFIED",
                "ROUTE_UNSUPPORTED",
                "REMEDY_EXHAUSTED",
                "REMEDY_NO_PROGRESS",
                "LANE_UNAVAILABLE",
                "BUDGET_DOES_NOT_FIT",
                "SECTION_UNAVAILABLE",
            }
        ),
        conditions=frozenset({"blocked"}),
    ),
    Row(
        10,
        NodeClass.BLOCKED,
        codes=frozenset({"FOUND_INCOMPATIBLE", "CREDENTIAL_STALE", "FOUND_UNHEALTHY"}),
        conditions=frozenset({"incompatible"}),
    ),
    Row(11, NodeClass.FAILED, conditions=frozenset({"failed"}), catch_all=True),
)


def _has_applied_remedy(tickets: Iterable[TicketLike]) -> bool:
    """A confirmed APPLIED ticket whose `remedy` is set (K7, WR-TERM-4)."""
    return any(
        t.remedy is not None and t.confirmation is not None and t.confirmation.status == "applied"
        for t in tickets
    )


def _matches(row: Row, end: NodeEndLike, name: str | None, repaired: bool) -> bool:
    if row.unreachable:
        return False
    if row.cut and end.cut is not None:
        return True
    if row.condition_none and end.condition is None:
        return True
    if name is not None and name in row.codes:
        return True
    if end.condition is not None and end.condition in row.conditions:
        return repaired if row.remedy else True
    return row.catch_all


def node_class(end: NodeEndLike, tickets: Sequence[TicketLike] = ()) -> NodeClass | None:
    """B4-T2: the class of the vertex's `NodeEnd`, or `None` unless it is a candidate (a cut end,
    or a composite with no condition of its own)."""
    name = _v11_name(end.code) if end.code is not None else None
    repaired = _has_applied_remedy(tickets)
    for row in NODE_CLASS_ROWS:
        if _matches(row, end, name, repaired):
            return row.klass
    return NodeClass.FAILED  # unreachable: row 11 is a catch-all


def listing(end: NodeEndLike | None, has_entries: bool) -> Listing:
    """How a vertex is listed (B4 `Listing`): no `NodeEnd` is UNENDED if the vertex has entries and
    NOT_STARTED if not; a cut end is STOPPED or NOT_STARTED; a composite with no condition of its
    own is ROLLED_UP; anything else is a CANDIDATE."""
    if end is None:
        return Listing.UNENDED if has_entries else Listing.NOT_STARTED
    if end.cut is not None:
        return Listing.STOPPED if end.cut == "stopped" else Listing.NOT_STARTED
    if end.condition is None:
        return Listing.ROLLED_UP
    return Listing.CANDIDATE


def resource_disposition(
    end: NodeEndLike, tickets: Sequence[TicketLike] = ()
) -> ResourceDisposition | None:
    """B4-C3: for a PASSED or REPAIRED node, REPAIRED if its record holds a confirmed APPLIED
    ticket with `remedy` set, else STARTED for provenance CREATED and REUSED for FOUND."""
    klass = node_class(end, tickets)
    if klass not in (NodeClass.PASSED, NodeClass.REPAIRED):
        return None
    if _has_applied_remedy(tickets):
        return ResourceDisposition.REPAIRED
    if end.provenance == "created":
        return ResourceDisposition.STARTED
    if end.provenance == "found":
        return ResourceDisposition.REUSED
    return None


# ---- B4-C4 / B4-C8


@final
@dataclass(frozen=True, slots=True)
class Candidate:
    """A vertex that reached a condition itself: its class, its code and its precedence ordinal."""

    path: str
    node_class: NodeClass
    code: str | None
    ordinal: int


def origin_of(code: str | None) -> Origin:
    """WHOLE_ROOT_TRIGGER iff the code is UNIT_RAISED (B4-T1), else NODE_REACHED."""
    return Origin.WHOLE_ROOT_TRIGGER if code == vocab.UNIT_RAISED else Origin.NODE_REACHED


def key(candidate: Candidate) -> tuple[int, int, int]:
    """B4-C4: `(class rank, origin rank, precedence ordinal)`, ascending, class rank first."""
    return (
        RANK[candidate.node_class],
        int(origin_of(candidate.code)),
        candidate.ordinal,
    )


def primary(candidates: Iterable[Candidate]) -> Candidate | None:
    """The key-min candidate (independent of the order candidates are given), or `None`."""
    return min(candidates, key=key, default=None)


def roll_up(candidates: Iterable[Candidate], children_satisfied: bool) -> NodeClass:
    """B4-C8: a composite's class is the key-min class over its subtree's candidates; with none it
    is PASSED if every selected child is SATISFIED, else EXECUTION_ERROR (the B4-C4 defect case)."""
    best = primary(candidates)
    if best is not None:
        return best.node_class
    return NodeClass.PASSED if children_satisfied else NodeClass.EXECUTION_ERROR


# ---- B4-T3

_OUTCOME: dict[NodeClass, OutcomeClass] = {
    NodeClass.EXECUTION_ERROR: OutcomeClass.EXECUTION_ERROR,
    NodeClass.UNENCODABLE_RESULT: OutcomeClass.EXECUTION_ERROR,
    NodeClass.FAILED: OutcomeClass.FAILED,
    NodeClass.TIMED_OUT: OutcomeClass.TIMED_OUT,
    NodeClass.EXHAUSTED: EXHAUSTED_AS,
    NodeClass.BLOCKED: OutcomeClass.BLOCKED,
    NodeClass.REPAIRED: OutcomeClass.PASSED,
    NodeClass.PASSED: OutcomeClass.PASSED,
}


def outcome_of(klass: NodeClass) -> OutcomeClass:
    """B4-T3: the outcome of a key-decided answer whose primary has this class."""
    return _OUTCOME[klass]
