"""Vocabulary value types the loop, the join and the unit contract share (L.SV-5.3).

Frozen, slotted, stdlib-only transcriptions of the shared vocabulary's V-1 (`NodePath`,
`Lineage`), V-2 (`Goal`, `StopCause`, `CancelSignal`), V-3 (`Provenance`, `Condition`,
`Observation`, `Verdict`, `NodeTerms` and their parts), V-4 (`FoundRef`, `OwnedHandle`,
`CreatedHandle`, `TicketRefusal`, `StepKind`), V-9 (`HostScopeReading`) and V-13's `EvidenceSink`.
Pure data plus two protocols: no method, no default beyond the ones the contract writes (and the
two marked on `NodeTerms`).

`trestle.workflow` imports no lane codec and no host module (C.5 step 4, B2-I5), so these types are
this side's only definition of the shapes; the SA-01 drift node ties the lane's classes to the
record view built from them by field name (A1c2-11). Names are the vocabulary's, in its order.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol, final

from trestle.workflow.declarations import (
    EffectId,
    HostScopeRef,
    HumanAction,
    JsonValue,
    LoopFlags,
    RemedyDeclaration,
    StableCode,
    WaitPolicy,
)

type Instant = datetime
type RunId = str
type BoundedText = str  # UTF-8, at most TEXT_MAX (V-13); detail, message, excerpt

# ---- V-1


@final
@dataclass(frozen=True, slots=True)
class NodePath:
    segments: tuple[str, ...]


@final
@dataclass(frozen=True, slots=True)
class Lineage:
    root_run_id: RunId
    path: NodePath


# ---- V-2


class Goal(StrEnum):
    CONVERGE = "converge"
    RELEASE = "release"


class StopCause(StrEnum):
    CANCEL = "cancel"
    RELEASE_POINT = "release_point"


class CancelSignal(Protocol):
    @property
    def requested(self) -> bool: ...

    def cause(self) -> StopCause | None: ...

    def wait(self, timeout: timedelta) -> bool: ...


class EvidenceSink(Protocol):
    def event(self, kind: str, fields: Mapping[str, JsonValue]) -> None: ...


# ---- V-3


class Provenance(StrEnum):
    ABSENT = "absent"
    FOUND = "found"
    CLAIMED = "claimed"
    CREATED = "created"


class Condition(StrEnum):
    SATISFIED = "satisfied"
    UNSATISFIED = "unsatisfied"
    CONVERGING = "converging"
    STALE = "stale"
    IN_DOUBT = "in_doubt"
    INCOMPATIBLE = "incompatible"
    BLOCKED = "blocked"
    FAILED = "failed"


class Resend(StrEnum):
    SUCCEEDS_AFTER_ACTION = "succeeds_after_action"
    WILL_NOT_SUCCEED = "will_not_succeed"
    UNKNOWN = "unknown"


class ConfirmationStatus(StrEnum):
    APPLIED = "applied"
    NOT_APPLIED = "not_applied"
    UNKNOWN = "unknown"


@final
@dataclass(frozen=True, slots=True)
class Confirmation:
    status: ConfirmationStatus
    code: StableCode | None
    identity: str | None


@final
@dataclass(frozen=True, slots=True)
class CheckResult:
    satisfied: bool
    code: StableCode | None
    detail: BoundedText


@final
@dataclass(frozen=True, slots=True)
class CurrencyFact:
    subject: HostScopeRef
    observed_generation: str
    valid_until: Instant | None
    older: StableCode | None = None


@final
@dataclass(frozen=True, slots=True)
class FoundRef:
    resource_kind: str
    selector: str
    observed_at: Instant


@final
@dataclass(frozen=True, slots=True)
class Observation:
    present: bool
    selector_present: bool
    identity_proven: bool
    configuration_compatible: bool
    postcondition: CheckResult
    preconditions: tuple[tuple[str, CheckResult], ...]
    currency: tuple[CurrencyFact, ...]
    found: tuple[FoundRef, ...]
    code: StableCode | None
    payload: JsonValue


@final
@dataclass(frozen=True, slots=True)
class TestCounts:
    __test__ = False  # not a pytest class

    passed: int
    failed: int
    errors: int
    skipped: int


@final
@dataclass(frozen=True, slots=True)
class RecordedResult:
    passed: bool
    code: StableCode | None
    counts: TestCounts | None


@final
@dataclass(frozen=True, slots=True)
class RemedyGrant:
    code: StableCode
    effect: EffectId
    attempt: int


@final
@dataclass(frozen=True, slots=True)
class AttemptSummary:
    attempts: int
    last_status: ConfirmationStatus | None
    last_code: StableCode | None


@final
@dataclass(frozen=True, slots=True)
class ClockReading:
    now: Instant
    root_deadline: Instant
    release_point: Instant


@final
@dataclass(frozen=True, slots=True)
class NodeTerms:
    """The declared data the join reads; built by the loop from the node's declaration.

    `path` and `currency_margin` are the two fields this transcription adds to V-3's list, both
    data and never flags (V-6.1 item 1): `path` renders V-11.1's `{path}` where the record holds no
    entry to read it from (J-24 before any ticket); `currency_margin` is J-25's "margin" (V-3.1),
    which no other V-3 input carries and which the loop must read from the operator limits, since
    `trestle.workflow` does not import `clock.py` (C.5 step 4). Both default to "not supplied".
    """

    flags: LoopFlags
    retryable: frozenset[StableCode]
    remedies: tuple[RemedyDeclaration, ...]
    wait: WaitPolicy
    budget: timedelta
    max_attempts: int
    slice_end: Instant
    path: NodePath | None = None
    currency_margin: timedelta = timedelta(0)


@dataclass(frozen=True, slots=True)
class OwnedHandle:
    """Sealed: exactly one subclass, `CreatedHandle`, in this delivery (V-4); no public constructor
    is exported to plugin code."""

    lineage: Lineage
    effect: EffectId
    selector: str
    release: object  # the creation ticket's recorded V-10 descriptor; the join never reads it


@final
@dataclass(frozen=True, slots=True)
class CreatedHandle(OwnedHandle):
    """Confirmed creation by this root: owned effects and release accept it (V-4)."""


@final
@dataclass(frozen=True, slots=True)
class Verdict:
    provenance: Provenance
    condition: Condition
    code: StableCode | None
    human_action: HumanAction | None
    resend: Resend | None
    currency: tuple[CurrencyFact, ...]
    attempts: AttemptSummary
    remedy: RemedyGrant | None
    owned: tuple[OwnedHandle, ...]
    found: tuple[FoundRef, ...]


# ---- V-4 refusals and step kinds


class TicketRefusal(StrEnum):
    ONCE_ALREADY_ISSUED = "once_already_issued"
    IN_DOUBT = "in_doubt"
    ATTEMPTS_SPENT = "attempts_spent"
    UNDECLARED_EFFECT = "undeclared_effect"
    WRONG_FACET_CLASS = "wrong_facet_class"
    RELEASE_POINT_PASSED = "release_point_passed"
    LANE_UNAVAILABLE = "lane_unavailable"
    PLAN_NOT_RECORDED = "plan_not_recorded"


class StepKind(StrEnum):
    BLOCKED = "blocked"
    FAILED = "failed"
    NO_ACTION = "no_action"


# ---- V-9


@final
@dataclass(frozen=True, slots=True)
class HostScopeReading:
    readings: tuple[tuple[HostScopeRef, str, Instant], ...]
