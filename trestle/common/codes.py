"""Stable error codes."""

from trestle.common.plan import vocabulary as _vocab

SERVICE_DRAINING = "admission.service_draining"
PLUGIN_NOT_FOUND = "admission.plugin_not_found"
IMPORT_FAILED = "admission.import_failed"
INVALID_ARGS = "admission.invalid_args"
QUEUE_FULL = "admission.queue_full"
NOT_ALLOWLISTED = "admission.not_allowlisted"
IDEMPOTENCY_KEY_CONFLICT = "admission.idempotency_key_conflict"
BUDGET_DOES_NOT_FIT = "admission.budget_does_not_fit"
# L.SV-3.5 (TM-B2-1): temporary, refused only in `Admission.admit` before any run id.
ADMISSION_PLAN_MULTI_VERTEX_UNSUPPORTED = "admission.plan_multi_vertex_unsupported"
# L.SL-8.2 (MC-06 seed, retryable): the environment's lease holder leaves too little time before
# this request's would-be deadline; refused before any run id.
ADMISSION_ENVIRONMENT_BUSY = "admission.environment_busy"
NOT_IMPLEMENTED = "projection.not_implemented"
INVALID_HANDLE = "projection.invalid_handle"
NOT_OWNER = "projection.not_owner"
# L.TR-2.5 (MC-06, V-11; OQ-27 assumed default): cancel is addressed to the root, never a child.
CANCEL_NOT_ROOT = "projection.cancel_not_root"
OUTSIDE_WINDOW = "projection.outside_window"
CANCEL_ACCEPTED = "projection.cancel_accepted"
PIN_ACCEPTED = "projection.pin_accepted"
UNPIN_ACCEPTED = "projection.unpin_accepted"
NOT_FINALIZED = "projection.not_finalized"
CURSOR_EXPIRED = "projection.cursor_expired"
INVALID_VIEW = "projection.invalid_view"
NOT_FOUND = "projection.not_found"
PROJECTION_INVALID_ARGS = "projection.invalid_args"
MISSING = "projection.missing"
EXPIRED = "projection.expired"
TERMINAL_WAIT_EXCEEDED = "projection.terminal_wait_exceeded"
PUBLICATION_INVALID_SOURCE = "publication.invalid_source"
PUBLICATION_NO_ENTRYPOINT = "publication.no_entrypoint"
PUBLICATION_NAME_MISMATCH = "publication.name_mismatch"
PUBLICATION_SOURCE_TOO_LARGE = "publication.source_too_large"
PUBLICATION_VALIDATION_FAILED = "publication.validation_failed"
PUBLICATION_DECLARATION_INVALID = "publication.declaration_invalid"
PUBLICATION_ENV_ARG_MISSING = "publication.env_arg_missing"

# MC-CORE-04: the execution-code vocabulary, additive only (DM-16). A run that started and ended
# without an answer carries exactly one of these in its `error_record` (MC-15).
EXECUTION_IMPORT_FAILED = "execution.import_failed"
EXECUTION_BIND_FAILED = "execution.bind_failed"
EXECUTION_PLUGIN_RAISED = "execution.plugin_raised"
EXECUTION_RESULT_UNENCODABLE = "execution.result_unencodable"
EXECUTION_PROVENANCE_MISMATCH = "execution.provenance_mismatch"
EXECUTION_CANCELLED = "execution.cancelled"
EXECUTION_DEADLINE_EXCEEDED = "execution.deadline_exceeded"
EXECUTION_WORKER_EXIT = "execution.worker_exit"
EXECUTION_INTERRUPTED = "execution.interrupted"


# L.SV-4.2 (DM-16): the single-level node and publication codes of `trestle.common.plan.vocabulary`
# (V-11; each the snake of its V-11 name), re-exported here so one module lists every wire code.
# The vocabulary defines the values; core's own spellings (EXECUTION_WORKER_EXIT,
# EXECUTION_INTERRUPTED, EXECUTION_CANCELLED, EXECUTION_DEADLINE_EXCEEDED,
# EXECUTION_RESULT_UNENCODABLE, BUDGET_DOES_NOT_FIT) are the same strings and stay as they are.
DECLARATION_STALE = _vocab.DECLARATION_STALE
TICKET_REFUSED = _vocab.TICKET_REFUSED

# L.TR-0.4 (DM-16): the tree publication refusals of `trestle.common.plan.vocabulary` (V-11 places
# the whole-tree check at publication), re-exported like the single-level set above.
PUBLICATION_UNIT_UNRESOLVED = _vocab.PUBLICATION_UNIT_UNRESOLVED
PUBLICATION_DEPENDENCY_CYCLE = _vocab.PUBLICATION_DEPENDENCY_CYCLE
PUBLICATION_DECLARATION_CONFLICT = _vocab.PUBLICATION_DECLARATION_CONFLICT
PUBLICATION_PLAN_PRECONDITION_UNCOVERED = _vocab.PUBLICATION_PLAN_PRECONDITION_UNCOVERED
UNIT_RAISED = _vocab.UNIT_RAISED
LANE_UNAVAILABLE = _vocab.LANE_UNAVAILABLE
STOP_SEEN = _vocab.STOP_SEEN
EFFECT_UNCONFIRMED = _vocab.EFFECT_UNCONFIRMED
PLAN_PRECONDITION_UNCOVERED = _vocab.PLAN_PRECONDITION_UNCOVERED
PRECONDITION_UNSATISFIED = _vocab.PRECONDITION_UNSATISFIED
POSTCONDITION_TIMEOUT = _vocab.POSTCONDITION_TIMEOUT
REMEDY_EXHAUSTED = _vocab.REMEDY_EXHAUSTED
REMEDY_NO_PROGRESS = _vocab.REMEDY_NO_PROGRESS
FOUND_UNHEALTHY = _vocab.FOUND_UNHEALTHY
FOUND_INCOMPATIBLE = _vocab.FOUND_INCOMPATIBLE
CARVE_EXCEEDED = _vocab.CARVE_EXCEEDED
CURRENCY_UNCONFIRMED = _vocab.CURRENCY_UNCONFIRMED
VERTEX_UNENDED = _vocab.VERTEX_UNENDED
PLAN_CONTRACT_MISSING = _vocab.PLAN_CONTRACT_MISSING
SAFE_START_VERB_INVALID = _vocab.SAFE_START_VERB_INVALID
FACET_LIFETIME_MISMATCH = _vocab.FACET_LIFETIME_MISMATCH
RELEASE_TIMEOUT_MISSING = _vocab.RELEASE_TIMEOUT_MISSING
MAX_ATTEMPTS_INVALID = _vocab.MAX_ATTEMPTS_INVALID
RELEASE_EFFECT_ONCE = _vocab.RELEASE_EFFECT_ONCE
FLAGS_CONTRADICT_TYPE = _vocab.FLAGS_CONTRADICT_TYPE
RECORDED_WITH_REMEDIES = _vocab.RECORDED_WITH_REMEDIES
OWNED_REMEDY_ON_FOUND = _vocab.OWNED_REMEDY_ON_FOUND
BUDGET_EXCEEDS_LEAF = _vocab.BUDGET_EXCEEDS_LEAF

# L.SV-3.5 (DM-16): the plan refusal codes of the vocabulary (V-11), re-exported beside core's own
# `BUDGET_DOES_NOT_FIT` (the same string; V-11 gives it one spelling, AM-7).
BOUND_EXCEEDED = _vocab.BOUND_EXCEEDED
UNKNOWN_IDENTIFIER = _vocab.UNKNOWN_IDENTIFIER
UNIT_UNRESOLVED = _vocab.UNIT_UNRESOLVED
DEPENDENCY_CYCLE = _vocab.DEPENDENCY_CYCLE
DECLARATION_CONFLICT = _vocab.DECLARATION_CONFLICT
LEASE_SET_UNDECIDABLE = _vocab.LEASE_SET_UNDECIDABLE
ROUTE_UNSUPPORTED = _vocab.ROUTE_UNSUPPORTED

# L.NW-2.3 (MC-B-12, DM-16): the adapter half of Slice B's code set, defined in the vocabulary
# (V-11 names, `<origin>.<snake>` values) and re-exported here. `TOOLCHAIN_MISSING` and
# `CREDENTIAL_INTERACTIVE` keep the `execution.*` spelling `trestle.workflow.codes` already
# gives them.
DOCKER_CLI_MISSING = _vocab.DOCKER_CLI_MISSING
DOCKER_ENGINE_UNREACHABLE = _vocab.DOCKER_ENGINE_UNREACHABLE
TOOLCHAIN_MISSING = _vocab.TOOLCHAIN_MISSING
TOOLCHAIN_INTERFACE_DRIFT = _vocab.TOOLCHAIN_INTERFACE_DRIFT
COMPOSE_DEFINITION_INVALID = _vocab.COMPOSE_DEFINITION_INVALID
CREDENTIAL_INTERACTIVE = _vocab.CREDENTIAL_INTERACTIVE
GRANT_ISSUER_UNREACHABLE = _vocab.GRANT_ISSUER_UNREACHABLE

EXECUTION_CODES: frozenset[str] = frozenset(
    {
        EXECUTION_IMPORT_FAILED,
        EXECUTION_BIND_FAILED,
        EXECUTION_PLUGIN_RAISED,
        EXECUTION_RESULT_UNENCODABLE,
        EXECUTION_PROVENANCE_MISMATCH,
        EXECUTION_CANCELLED,
        EXECUTION_DEADLINE_EXCEEDED,
        EXECUTION_WORKER_EXIT,
        EXECUTION_INTERRUPTED,
    }
) | frozenset(
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
    }
)
