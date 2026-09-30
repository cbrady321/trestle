"""Plan vocabulary: the plan refusal codes (V-11; L.SV-3.1), B4's node vocabulary, the
single-level code set (L.SV-4.1) and Slice B's adapter codes (L.NW-2.3).

Every code is the snake of its V-11 name behind the origin that raises it (V-11: names are
provisional, the set is closed, meanings bind; RIPPLE-MAP N4). `V11` maps each V-11 name A-1 uses
to its one spelling. The B4 enums are transcribed in B4's declaration order and invent nothing;
`OutcomeClass` stays MC-17's, in `trestle.common.outcome`. Pure: stdlib only, so the host, the
child and `trestle.workflow` read one spelling. `codes.py` re-exports the wire codes (L.SV-4.2).

`BUDGET_DOES_NOT_FIT` keeps core's spelling (`trestle.common.codes.BUDGET_DOES_NOT_FIT`, L.CL-C1.5):
V-11 gives the code one spelling for admission and for the in-run dispatch re-check, so no second
constant may carry another value.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import IntEnum, StrEnum

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

# ---- node codes (V-11.3 and the loop, join and facets; L.SV-4.1)

DECLARATION_STALE = "execution.declaration_stale"
TICKET_REFUSED = "execution.ticket_refused"
UNIT_RAISED = "execution.unit_raised"
LANE_UNAVAILABLE = "execution.lane_unavailable"
STOP_SEEN = "execution.stop_seen"
EFFECT_UNCONFIRMED = "execution.effect_unconfirmed"
PLAN_PRECONDITION_UNCOVERED = "execution.plan_precondition_uncovered"
PRECONDITION_UNSATISFIED = "execution.precondition_unsatisfied"
POSTCONDITION_TIMEOUT = "execution.postcondition_timeout"
REMEDY_EXHAUSTED = "execution.remedy_exhausted"
REMEDY_NO_PROGRESS = "execution.remedy_no_progress"
FOUND_UNHEALTHY = "execution.found_unhealthy"
FOUND_INCOMPATIBLE = "execution.found_incompatible"
CARVE_EXCEEDED = "execution.carve_exceeded"
CURRENCY_UNCONFIRMED = "execution.currency_unconfirmed"
REALIZATION_ABSENT = "execution.realization_absent"  # V-7.4 / V-11: a unit's Blocked (L.TR-5.2)
VERTEX_UNENDED = "execution.vertex_unended"

# V-11's remaining execution-error codes keep core's spellings (MC-CORE-04).
WORKER_EXIT = "execution.worker_exit"
RESULT_UNENCODABLE = "execution.result_unencodable"
EXECUTION_RESTART = "execution.interrupted"
EXECUTION_CANCELLED = "execution.cancelled"
EXECUTION_DEADLINE = "execution.deadline_exceeded"

# ---- publication grounds B1 leaves unnamed (B1-E1/E2/E3; "codes provisional")

PLAN_CONTRACT_MISSING = "publication.plan_contract_missing"
SAFE_START_VERB_INVALID = "publication.safe_start_verb_invalid"
FACET_LIFETIME_MISMATCH = "publication.facet_lifetime_mismatch"
RELEASE_TIMEOUT_MISSING = "publication.release_timeout_missing"
MAX_ATTEMPTS_INVALID = "publication.max_attempts_invalid"
RELEASE_EFFECT_ONCE = "publication.release_effect_once"
FLAGS_CONTRADICT_TYPE = "publication.flags_contradict_type"
RECORDED_WITH_REMEDIES = "publication.recorded_with_remedies"
OWNED_REMEDY_ON_FOUND = "publication.owned_remedy_on_found"
BUDGET_EXCEEDS_LEAF = "publication.budget_exceeds_leaf"

# ---- tree publication refusals (V-11: the whole tree check sits at publication; L.TR-0.4)
#
# `UNIT_UNRESOLVED`, `DEPENDENCY_CYCLE` and `DECLARATION_CONFLICT` place their check at publication
# for what the declaration alone decides (no snapshot is promoted, so no run id exists); admission
# keeps its own spellings above as V-11's defensive second reach. `PLAN_PRECONDITION_UNCOVERED` at
# publication is the root-entry eligibility refusal (OQ-31, `refuse_at_publication`) and the
# cross-node coverage refusal (L.TR-1.5).

PUBLICATION_UNIT_UNRESOLVED = "publication.unit_unresolved"
PUBLICATION_DEPENDENCY_CYCLE = "publication.dependency_cycle"
PUBLICATION_DECLARATION_CONFLICT = "publication.declaration_conflict"
PUBLICATION_PLAN_PRECONDITION_UNCOVERED = "publication.plan_precondition_uncovered"

TREE_PUBLICATION_CODES: frozenset[str] = frozenset(
    {
        PUBLICATION_UNIT_UNRESOLVED,
        PUBLICATION_DEPENDENCY_CYCLE,
        PUBLICATION_DECLARATION_CONFLICT,
        PUBLICATION_PLAN_PRECONDITION_UNCOVERED,
    }
)

SINGLE_LEVEL_CODES: frozenset[str] = frozenset(
    {
        DECLARATION_STALE,
        TICKET_REFUSED,
        UNIT_RAISED,
        LANE_UNAVAILABLE,
        STOP_SEEN,
        EFFECT_UNCONFIRMED,
        PLAN_PRECONDITION_UNCOVERED,
        PRECONDITION_UNSATISFIED,
        POSTCONDITION_TIMEOUT,
        REMEDY_EXHAUSTED,
        REMEDY_NO_PROGRESS,
        FOUND_UNHEALTHY,
        FOUND_INCOMPATIBLE,
        CARVE_EXCEEDED,
        CURRENCY_UNCONFIRMED,
        VERTEX_UNENDED,
        PLAN_CONTRACT_MISSING,
        SAFE_START_VERB_INVALID,
        FACET_LIFETIME_MISMATCH,
        RELEASE_TIMEOUT_MISSING,
        MAX_ATTEMPTS_INVALID,
        RELEASE_EFFECT_ONCE,
        FLAGS_CONTRADICT_TYPE,
        RECORDED_WITH_REMEDIES,
        OWNED_REMEDY_ON_FOUND,
        BUDGET_EXCEEDS_LEAF,
    }
)

# ---- Slice B's adapter half (L.NW-2.3; MC-B-12; B3-E1). V-11 codes only: no adapter code outside
# the closed set exists, and a code carries no class of its own (B4-T2 decides a node's class over
# its `NodeEnd`). A code that A-1 already spelled keeps its one spelling (V-11 gives every code
# one): `TOOLCHAIN_MISSING` and `CREDENTIAL_INTERACTIVE` are the `execution.*` values
# `trestle.workflow.codes` compares in the join (J-5a) and the real command port returns; the
# five others are `adapter.*`.

DOCKER_CLI_MISSING = "adapter.docker_cli_missing"
DOCKER_ENGINE_UNREACHABLE = "adapter.docker_engine_unreachable"
TOOLCHAIN_MISSING = "execution.toolchain_missing"
TOOLCHAIN_INTERFACE_DRIFT = "adapter.toolchain_interface_drift"
COMPOSE_DEFINITION_INVALID = "adapter.compose_definition_invalid"
CREDENTIAL_INTERACTIVE = "execution.credential_interactive"
GRANT_ISSUER_UNREACHABLE = "adapter.grant_issuer_unreachable"
# L.RB-6.3.fix1 (ADD-code-provision-store-unreadable): the one code V-11 lacked for "the
# environment's authoritative record store did not answer" (a wrong password, a stopped
# container, a failed psql): the provisioning probe could not observe, so nothing is claimed
# absent (V-3.8). It is neither an unreachable engine nor an output outside the pinned shape.
PROVISION_STORE_UNREADABLE = "adapter.provision_store_unreadable"

ADAPTER_CODES: frozenset[str] = frozenset(
    {
        DOCKER_CLI_MISSING,
        DOCKER_ENGINE_UNREACHABLE,
        TOOLCHAIN_MISSING,
        TOOLCHAIN_INTERFACE_DRIFT,
        COMPOSE_DEFINITION_INVALID,
        CREDENTIAL_INTERACTIVE,
        GRANT_ISSUER_UNREACHABLE,
        PROVISION_STORE_UNREADABLE,
    }
)

# Each V-11 name A-1 uses -> its one plan spelling. The in-run dispatch re-check reuses
# admission.budget_does_not_fit (V-11: one spelling; no execution.*_at_dispatch code exists).
V11: Mapping[str, str] = {
    "UNKNOWN_IDENTIFIER": UNKNOWN_IDENTIFIER,
    "DECLARATION_CONFLICT": DECLARATION_CONFLICT,
    "DEPENDENCY_CYCLE": DEPENDENCY_CYCLE,
    "UNIT_UNRESOLVED": UNIT_UNRESOLVED,
    "PLAN_CONTRACT_MISSING": PLAN_CONTRACT_MISSING,
    "PLAN_PRECONDITION_UNCOVERED": PLAN_PRECONDITION_UNCOVERED,
    "BOUND_EXCEEDED": BOUND_EXCEEDED,
    "BUDGET_DOES_NOT_FIT": BUDGET_DOES_NOT_FIT,
    "LEASE_SET_UNDECIDABLE": LEASE_SET_UNDECIDABLE,
    "ROUTE_UNSUPPORTED": ROUTE_UNSUPPORTED,
    "DECLARATION_STALE": DECLARATION_STALE,
    "REALIZATION_ABSENT": REALIZATION_ABSENT,
    "EFFECT_UNCONFIRMED": EFFECT_UNCONFIRMED,
    "TICKET_REFUSED": TICKET_REFUSED,
    "EXECUTION_CANCELLED": EXECUTION_CANCELLED,
    "EXECUTION_DEADLINE": EXECUTION_DEADLINE,
    "FOUND_UNHEALTHY": FOUND_UNHEALTHY,
    "FOUND_INCOMPATIBLE": FOUND_INCOMPATIBLE,
    "UNIT_RAISED": UNIT_RAISED,
    "POSTCONDITION_TIMEOUT": POSTCONDITION_TIMEOUT,
    "PRECONDITION_UNSATISFIED": PRECONDITION_UNSATISFIED,
    "LANE_UNAVAILABLE": LANE_UNAVAILABLE,
    "STOP_SEEN": STOP_SEEN,
    "REMEDY_EXHAUSTED": REMEDY_EXHAUSTED,
    "REMEDY_NO_PROGRESS": REMEDY_NO_PROGRESS,
    "CARVE_EXCEEDED": CARVE_EXCEEDED,
    "CURRENCY_UNCONFIRMED": CURRENCY_UNCONFIRMED,
    "WORKER_EXIT": WORKER_EXIT,
    "EXECUTION_RESTART": EXECUTION_RESTART,
    "VERTEX_UNENDED": VERTEX_UNENDED,
    "RESULT_UNENCODABLE": RESULT_UNENCODABLE,
}


# ---- B4's node vocabulary (interface-terminal-answer.md), in B4's declaration order


class NodeClass(StrEnum):
    """Class rank = declaration order, 0 highest (B4-T1)."""

    EXECUTION_ERROR = "execution_error"
    UNENCODABLE_RESULT = "unencodable_result"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    EXHAUSTED = "exhausted"
    BLOCKED = "blocked"
    REPAIRED = "repaired"
    PASSED = "passed"


class Origin(IntEnum):
    """Origin rank (B4-T1)."""

    WHOLE_ROOT_TRIGGER = 0  # code UNIT_RAISED: the only whole-root trigger under fail-fast
    NODE_REACHED = 1


class Listing(StrEnum):
    CANDIDATE = "candidate"  # reached its own condition (B4-C4)
    ROLLED_UP = "rolled_up"  # a composite that ended normally; class rolled up by the same key
    STOPPED = "stopped"
    NOT_STARTED = "not_started"
    UNENDED = "unended"  # entries but no NodeEnd; with neither entries nor a NodeEnd: NOT_STARTED


class ResourceDisposition(StrEnum):
    """WR-OWN-1, for a node whose class is PASSED or REPAIRED."""

    REUSED = "reused"
    STARTED = "started"
    REPAIRED = "repaired"


class RootStop(StrEnum):
    CANCEL = "cancel"
    RELEASE_POINT = "release_point"
    RESTART = "restart"
