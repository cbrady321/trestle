"""SA-03 in A-1 (L.SV-4.1): the precedence module transcribes B4's tables and nothing else.

The tables below are written out here independently of `trestle.common.plan.precedence` (from
`interfaces/interface-terminal-answer.md`, B4-T1, B4-T2, B4-T3), so a change to either side is a
red test rather than a silent drift. `vocabulary` must define every code of the single-level set
(a superset check: a later band adding codes never fails it).
"""

from __future__ import annotations

import pytest

from trestle.common.outcome import OutcomeClass
from trestle.common.plan import precedence
from trestle.common.plan import vocabulary as vocab
from trestle.common.plan.vocabulary import NodeClass, Origin

# B4-T1: rank ascending = higher precedence
B4_T1 = [
    (0, "execution_error", True),
    (1, "unencodable_result", False),
    (2, "failed", False),
    (3, "timed_out", False),
    (4, "exhausted", False),
    (5, "blocked", False),
    (6, "repaired", False),
    (7, "passed", False),
]

# B4-T2: (row, class, codes by V-11 name, conditions, needs a confirmed APPLIED remedy ticket)
B4_T2 = [
    (1, None, set(), set(), False),  # cut set, or a composite with condition None
    (2, "execution_error", {"UNIT_RAISED"}, set(), False),
    (3, "execution_error", {"DECLARATION_STALE"}, set(), False),
    (4, "unencodable_result", {"RESULT_UNENCODABLE"}, set(), False),  # unreachable for a tree node
    (5, "repaired", set(), {"satisfied"}, True),
    (6, "passed", set(), {"satisfied"}, False),
    (7, "timed_out", {"CARVE_EXCEEDED", "EXECUTION_DEADLINE"}, set(), False),
    (
        8,
        "exhausted",
        {
            "POSTCONDITION_TIMEOUT",
            "EFFECT_UNCONFIRMED",
            "CURRENCY_UNCONFIRMED",
            "CREDENTIAL_LIFETIME_INSUFFICIENT",
        },
        set(),
        False,
    ),
    (
        9,
        "blocked",
        {
            "PRECONDITION_UNSATISFIED",
            "ROUTE_UNSUPPORTED",
            "REMEDY_EXHAUSTED",
            "REMEDY_NO_PROGRESS",
            "LANE_UNAVAILABLE",
            "BUDGET_DOES_NOT_FIT",
            "SECTION_UNAVAILABLE",
        },
        {"blocked"},
        False,
    ),
    (
        10,
        "blocked",
        {"FOUND_INCOMPATIBLE", "CREDENTIAL_STALE", "FOUND_UNHEALTHY"},
        {"incompatible"},
        False,
    ),
    (11, "failed", set(), {"failed"}, False),  # and any other code
]

# B4-T3
B4_T3 = {
    "execution_error": "execution_error",
    "unencodable_result": "execution_error",
    "failed": "failed",
    "timed_out": "timed_out",
    "exhausted": "blocked",  # EXHAUSTED_AS (OQ-32)
    "blocked": "blocked",
    "repaired": "passed",
    "passed": "passed",
}

# the single-level code set of L.SV-4.1 (DM-16; A1c3-7), each `<origin>.<snake>`
SINGLE_LEVEL = {
    *(
        f"execution.{name}"
        for name in (
            "declaration_stale ticket_refused unit_raised lane_unavailable stop_seen "
            "effect_unconfirmed plan_precondition_uncovered precondition_unsatisfied "
            "postcondition_timeout remedy_exhausted remedy_no_progress found_unhealthy "
            "found_incompatible carve_exceeded currency_unconfirmed vertex_unended"
        ).split()
    ),
    *(
        f"publication.{name}"
        for name in (
            "plan_contract_missing safe_start_verb_invalid facet_lifetime_mismatch "
            "release_timeout_missing max_attempts_invalid release_effect_once "
            "flags_contradict_type recorded_with_remedies owned_remedy_on_found "
            "budget_exceeds_leaf"
        ).split()
    ),
}


@pytest.mark.parametrize("sa", ["SA-03"])
def test_rank_table_transcribes_b4_t1(sa: str) -> None:
    assert [c.value for c in NodeClass] == [name for _, name, _ in B4_T1]
    for rank, name, origin0 in B4_T1:
        assert precedence.RANK[NodeClass(name)] == rank
        # only the class rank 0 can hold an origin-0 candidate: node_class gives UNIT_RAISED it
        if origin0:
            assert NodeClass(name) is NodeClass.EXECUTION_ERROR
    assert [o.value for o in Origin] == [0, 1]
    assert Origin.WHOLE_ROOT_TRIGGER == 0 and Origin.NODE_REACHED == 1
    assert precedence.origin_of("execution.unit_raised") is Origin.WHOLE_ROOT_TRIGGER
    assert precedence.origin_of("execution.declaration_stale") is Origin.NODE_REACHED


@pytest.mark.parametrize("sa", ["SA-03"])
def test_node_class_rows_transcribe_b4_t2(sa: str) -> None:
    got = [
        (
            row.number,
            None if row.klass is None else row.klass.value,
            set(row.codes),
            set(row.conditions),
            row.remedy,
        )
        for row in precedence.NODE_CLASS_ROWS
    ]
    assert got == B4_T2
    assert [row.number for row in precedence.NODE_CLASS_ROWS if row.unreachable] == [4]


@pytest.mark.parametrize("sa", ["SA-03"])
def test_outcome_map_transcribes_b4_t3(sa: str) -> None:
    assert {k.value: precedence.outcome_of(k).value for k in NodeClass} == B4_T3
    assert set(B4_T3.values()) <= {o.value for o in OutcomeClass}


@pytest.mark.parametrize("sa", ["SA-03"])
def test_exhausted_as_is_blocked(sa: str) -> None:
    assert precedence.EXHAUSTED_AS is OutcomeClass.BLOCKED
    assert precedence.outcome_of(NodeClass.EXHAUSTED) is OutcomeClass.BLOCKED
    # one variant, no switch value: nothing in the module names another EXHAUSTED_AS
    assert [n for n in dir(precedence) if n.startswith("EXHAUSTED_AS")] == ["EXHAUSTED_AS"]
    # no NodeClass maps to FAILED except FAILED (B4-I2)
    assert [k for k in NodeClass if precedence.outcome_of(k) is OutcomeClass.FAILED] == [
        NodeClass.FAILED
    ]


@pytest.mark.parametrize("sa", ["SA-03"])
def test_vocabulary_covers_every_single_code(sa: str) -> None:
    defined = {v for k, v in vars(vocab).items() if k.isupper() and isinstance(v, str) and "." in v}
    assert SINGLE_LEVEL <= defined
    assert SINGLE_LEVEL <= vocab.SINGLE_LEVEL_CODES
    assert all(len(code) <= 64 and code.isascii() and code.isprintable() for code in defined)
    # V-11 names A-1 uses map to exactly one spelling each, and no spelling is used twice
    assert len(set(vocab.V11.values())) == len(vocab.V11)
    assert vocab.V11["BUDGET_DOES_NOT_FIT"] == "admission.budget_does_not_fit"
    assert vocab.V11["WORKER_EXIT"] == "execution.worker_exit"
    assert vocab.V11["RESULT_UNENCODABLE"] == "execution.result_unencodable"
    assert vocab.V11["EXECUTION_RESTART"] == "execution.interrupted"
    assert vocab.PLAN_REFUSAL_CODES <= set(vocab.V11.values())
    # core's spelling of the shared code is the vocabulary's
    from trestle.common import codes

    assert codes.BUDGET_DOES_NOT_FIT == vocab.BUDGET_DOES_NOT_FIT
