"""Stable error codes."""

SERVICE_DRAINING = "admission.service_draining"
PLUGIN_NOT_FOUND = "admission.plugin_not_found"
IMPORT_FAILED = "admission.import_failed"
INVALID_ARGS = "admission.invalid_args"
QUEUE_FULL = "admission.queue_full"
IDEMPOTENCY_KEY_CONFLICT = "admission.idempotency_key_conflict"
NOT_IMPLEMENTED = "projection.not_implemented"
INVALID_HANDLE = "projection.invalid_handle"
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
