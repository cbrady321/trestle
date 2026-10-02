"""B2's run services as the loop sees them (L.SV-5.2; MC-B2-02, B2-C3..C7, B2-C13, B2-C14).

`trestle.workflow` imports no server or child module and no lane codec (C.5 step 4, B2-I5), so
B2's `RunServices`, `AttemptLane` and `RunContext` are mirrored here as runtime-checkable
Protocols and the lane-side value types the lane's surface names but the workflow package has no
other home for (`AttemptTicket`, `PlanIdentity`, `NodeEnd`, `LaneRefusal`, `Cut`) as stdlib
frozen dataclasses. `trestle/child/run_services.py` implements the Protocols over the real lane
and converts at the boundary, field by field; the two sides are tied by the SA-01 drift node.

The record the loop reads is `units.NodeRecordView`, the only record shape (MC-B2-02). A node
path is `values.NodePath`; the lane's own paths are tuples and never cross this wall.

`sections()` is not part of the surface at one vertex: no host section is declared in A-1 (B2-C8
and OQ-26 are carried), so a `RunServices` here has no such member.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, final, runtime_checkable

from trestle.common.plan.compiler import AdmittedPlan as PlanAccepted
from trestle.plugin.surface import Context
from trestle.workflow.declarations import (
    EffectFacetClass,
    EffectId,
    HumanAction,
    Lifetime,
    Repeat,
    StableCode,
)
from trestle.workflow.units import NodeRecordView, StepView
from trestle.workflow.values import (
    CancelSignal,
    ClockReading,
    Condition,
    Confirmation,
    CreatedHandle,
    EvidenceSink,
    Instant,
    Lineage,
    NodePath,
    Provenance,
    RecordedResult,
    RemedyGrant,
    Resend,
    RunId,
    TicketRefusal,
)

type ChildRunId = str  # derived by the host from (root RunId, NodePath); opaque to every caller


class UnknownNode(Exception):  # noqa: N818 (B2-C4's name)
    """A path outside the admitted `selected_scope` (B2-C4): an execution error, never a new
    identity."""


@final
@dataclass(frozen=True, slots=True)
class AdmittedPlan:
    """B2's `AdmittedPlan` (B2-C3): the plan admission accepted, as recorded in the run's spec.
    `accepted` is the compiled plan (`PlanAccepted`'s fields: scope, carves, release slice, ranks,
    digest, `lane_entries`) with its digest, so the loop can prove it before the first effect."""

    lineage_root: RunId
    accepted: PlanAccepted


# ---- the lane-side value types the AttemptLane surface names


class LaneRefusal(StrEnum):
    """What a lane write returns instead of None (V-4, B2-C7)."""

    UNAVAILABLE = "unavailable"
    FULL = "full"
    OVER_BOUND = "over_bound"
    DUPLICATE = "duplicate"


class Cut(StrEnum):
    STOPPED = "stopped"
    NOT_STARTED = "not_started"


@final
@dataclass(frozen=True, slots=True)
class AttemptTicket:
    """V-4 `AttemptTicket`, issued only by the attempt lane after its issue entry is durable.
    `release` is the V-10 descriptor as the lane recorded it, in its wire mapping (the loop's
    ports transcribe the three forms under their names, L.SV-5.6)."""

    lineage: Lineage
    effect: EffectId
    facet: EffectFacetClass
    attempt: int
    repeat: Repeat
    lifetime: Lifetime
    release: object
    remedy: RemedyGrant | None


@final
@dataclass(frozen=True, slots=True)
class PlanIdentity:
    """The one `record_plan` entry's content: what the root proved before its first effect."""

    declaration_digest: str
    args_hash: str
    selection: tuple[tuple[NodePath, NodePath], ...]  # CHOICE path -> selected alternative
    observations_digest: str


@final
@dataclass(frozen=True, slots=True)
class NodeEnd:
    """V-4.8: a vertex's end, into the slot `record_plan` reserved for it."""

    lineage: Lineage
    at: Instant
    condition: Condition | None
    code: StableCode | None
    human_action: HumanAction | None
    resend: Resend | None
    provenance: Provenance | None
    cut: Cut | None


# ---- B2-C7


@runtime_checkable
class AttemptLane(Protocol):
    """The durable claim/attempt record (B2-C7) in the workflow package's own types. Order,
    durability, capacity and each refusal are B2-C7's and V-4's, cited by the lane
    (`trestle/child/attempt_lane.py`), never restated here. `release` is a V-10 descriptor: any
    object carrying one of the three forms' field names, or its wire mapping."""

    def record_plan(self, plan: PlanIdentity) -> None: ...

    def issue(
        self,
        lineage: Lineage,
        effect: EffectId,
        facet: EffectFacetClass,
        repeat: Repeat,
        lifetime: Lifetime,
        release: object,
        max_attempts: int,
        remedy: RemedyGrant | None,
    ) -> AttemptTicket | TicketRefusal: ...

    def confirm(
        self, ticket: AttemptTicket, confirmation: Confirmation
    ) -> CreatedHandle | None: ...

    def record_result(self, ticket: AttemptTicket, result: RecordedResult) -> None: ...

    def record_step(self, step: StepView) -> None | LaneRefusal: ...

    def record_released(self, handle: CreatedHandle, outcome: StableCode | None) -> None: ...

    def record_end(self, end: NodeEnd) -> None | LaneRefusal: ...

    def node_record(self, path: NodePath) -> NodeRecordView:
        """V-4.5's durable part, as the loop-side view (MC-B2-02)."""
        ...

    def committed_length(self) -> int: ...


# ---- B2-C3..C6, B2-C13


@runtime_checkable
class RunServices(Protocol):
    def admitted(self) -> AdmittedPlan: ...

    def lineage(self, path: NodePath) -> Lineage: ...

    def child_run_id(self, path: NodePath) -> ChildRunId: ...

    def clock(self) -> ClockReading: ...

    def slice_end(self, path: NodePath) -> Instant: ...

    def cancellation(self) -> CancelSignal: ...

    def release_point_reached(self) -> None:
        """The root saw its own slice end, which is the release point (P4): raise the
        release-point stop flag the host reads (B2-C6), before the goal flips. Idempotent. The
        host's clock may not have reached its release point yet (a different clock, a sleep or
        a clock step); the flag is what tells it the run stopped there."""
        ...

    def attempts(self) -> AttemptLane: ...

    def evidence(self) -> EvidenceSink: ...


@runtime_checkable
class FinalizationBounds(Protocol):
    """Two operator bounds the loop reads and B2's surface does not carry (L.SV-5.7): the reserve
    each parent holds back in a carve (the dispatch re-check's worst case needs it, B2-C5) and the
    finalization margin (J-25's currency margin, V-3.1). `trestle.workflow` may not import
    `clock.py` (C.5 step 4), so the child's services expose them; a `RunServices` without them is
    read as zero for both."""

    @property
    def finalization_reserve_s(self) -> float: ...

    @property
    def currency_margin_s(self) -> float: ...


# ---- B2-C14


@runtime_checkable
class RunContext(Context, Protocol):
    """Today's plugin `Context` plus the run's services. Additive: a plain plugin never reads
    `run_services`, and a plain plugin's context does not carry it."""

    @property
    def run_services(self) -> RunServices: ...


__all__ = [
    "AdmittedPlan",
    "AttemptLane",
    "AttemptTicket",
    "ChildRunId",
    "Cut",
    "FinalizationBounds",
    "LaneRefusal",
    "NodeEnd",
    "PlanAccepted",
    "PlanIdentity",
    "RunContext",
    "RunServices",
    "UnknownNode",
]
