"""Plan vocabulary: the plan refusal codes (V-11; L.SV-3.1).

Every code is the snake of its V-11 name behind the origin that raises it (V-11: names are
provisional, meanings bind; RIPPLE-MAP N4). This module starts with the plan refusal codes
`compile` (MC-23) returns; L.SV-4.1 adds B4's node vocabulary and the single-level code set.
Pure: stdlib only, so the host, the child and `trestle.workflow` read one spelling.

`BUDGET_DOES_NOT_FIT` keeps core's spelling (`trestle.common.codes.BUDGET_DOES_NOT_FIT`, L.CL-C1.5):
V-11 gives the code one spelling for admission and for the in-run dispatch re-check, so no second
constant may carry another value.
"""

from __future__ import annotations

BOUND_EXCEEDED = "admission.bound_exceeded"
UNKNOWN_IDENTIFIER = "admission.unknown_identifier"
UNIT_UNRESOLVED = "admission.unit_unresolved"
DEPENDENCY_CYCLE = "admission.dependency_cycle"
DECLARATION_CONFLICT = "admission.declaration_conflict"
BUDGET_DOES_NOT_FIT = "admission.budget_does_not_fit"
LEASE_SET_UNDECIDABLE = "admission.lease_set_undecidable"
ROUTE_UNSUPPORTED = "admission.route_unsupported"

PLAN_REFUSAL_CODES: frozenset[str] = frozenset(
    {
        BOUND_EXCEEDED,
        UNKNOWN_IDENTIFIER,
        UNIT_UNRESOLVED,
        DEPENDENCY_CYCLE,
        DECLARATION_CONFLICT,
        BUDGET_DOES_NOT_FIT,
        LEASE_SET_UNDECIDABLE,
        ROUTE_UNSUPPORTED,
    }
)
