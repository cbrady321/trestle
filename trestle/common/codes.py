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
NOT_IMPLEMENTED = "projection.not_implemented"
INVALID_HANDLE = "projection.invalid_handle"
NOT_OWNER = "projection.not_owner"
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


# L.SV-3.5 (DM-16): the plan refusal codes of the vocabulary (V-11), re-exported beside core's own
# `BUDGET_DOES_NOT_FIT` (the same string; V-11 gives it one spelling, AM-7).
BOUND_EXCEEDED = _vocab.BOUND_EXCEEDED
UNKNOWN_IDENTIFIER = _vocab.UNKNOWN_IDENTIFIER
UNIT_UNRESOLVED = _vocab.UNIT_UNRESOLVED
DEPENDENCY_CYCLE = _vocab.DEPENDENCY_CYCLE
DECLARATION_CONFLICT = _vocab.DECLARATION_CONFLICT
LEASE_SET_UNDECIDABLE = _vocab.LEASE_SET_UNDECIDABLE
ROUTE_UNSUPPORTED = _vocab.ROUTE_UNSUPPORTED

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
)
