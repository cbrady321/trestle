"""B1's work-unit contract types and the loop-side record view (L.SV-5.3).

B1-C1..C7: what a unit returns (`Acted`, `Blocked`, `Failed`, `NoAction`, `EffectRefused`), the
per-call capabilities the loop hands in (`ObserveContext`, `ActContext`, the facet protocols) and
the author-implemented leaf (`WorkUnit`). The loop-side record view (`NodeRecordView`) is the only
shape the pure join reads a node's record through (MC-B2-02): stdlib frozen dataclasses carrying
V-4's `TicketEntry` / `StepEntry` field names and never a second definition of V-4's entry shape;
they are tied to the lane's classes by the SA-01 drift node, by field name and wire-level type.

Imports: stdlib and this package's own value modules only (C.5 step 4). The V-5 facet marker
protocols (`ReadFacet`, `CreateFacet`, ...) are ports.py's (L.SV-5.6); until then the facet
protocols here take the port type unbounded, and `Ticketed[P]` is the structural placeholder
`trestle.workflow.facets` refines.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any, Protocol, final

from trestle.workflow import codes
from trestle.workflow.declarations import (
    EffectFacetClass,
    EffectId,
    HumanAction,
    LeafDeclaration,
    Lifetime,
    Repeat,
    StableCode,
)
from trestle.workflow.values import (
    BoundedText,
    CancelSignal,
    ClockReading,
    Confirmation,
    CreatedHandle,
    EvidenceSink,
    Instant,
    Lineage,
    Observation,
    RecordedResult,
    RemedyGrant,
    Resend,
    StepKind,
    TicketRefusal,
    Verdict,
)

# ---- what a unit returns (B1-C7)


@final
@dataclass(frozen=True, slots=True)
class Acted:
    """An effect was issued through a ticketed facet. The record, not this value, says which."""


@final
@dataclass(frozen=True, slots=True)
class Blocked:
    code: StableCode
    human_action: HumanAction
    resend: Resend


@final
@dataclass(frozen=True, slots=True)
class Failed:
    code: StableCode
    detail: BoundedText


@final
@dataclass(frozen=True, slots=True)
class NoAction:
    reason: StableCode


type Step = Acted | Blocked | Failed | NoAction


class EffectRefused(Exception):
    """Raised by a ticketed facet before any port call; the facet has already recorded the
    outcome (B1-E4)."""

    code: StableCode
    refusal: TicketRefusal

    def __init__(self, refusal: TicketRefusal, code: StableCode = codes.TICKET_REFUSED) -> None:
        super().__init__(f"{code}: {refusal.value}")
        self.code = code
        self.refusal = refusal


# ---- per-call capabilities the loop hands in


class ObserveContext(Protocol):
    @property
    def lineage(self) -> Lineage: ...

    @property
    def clock(self) -> ClockReading: ...

    @property
    def cancellation(self) -> CancelSignal: ...

    @property
    def evidence(self) -> EvidenceSink: ...


class ActContext(ObserveContext, Protocol):
    @property
    def remedy(self) -> RemedyGrant | None: ...


class Ticketed[P](Protocol):
    """`P` with every `ticket: AttemptTicket` parameter replaced by `effect: EffectId` (B1-C6).

    The member set is `P`'s own, which a Protocol cannot derive; this placeholder admits any member
    so a unit type-checks against it, and `trestle.workflow.facets` (L.SV-5.6) implements the
    five-step call.
    """

    def __getattr__(self, name: str) -> Callable[..., Any]: ...


class ReadFacets(Protocol):
    def read[R](self, port: type[R]) -> R: ...


class EffectFacets(ReadFacets, Protocol):
    def create[C](self, port: type[C]) -> Ticketed[C]: ...

    def owned[O](self, port: type[O]) -> Ticketed[O]: ...

    def safe_start[S](self, port: type[S]) -> Ticketed[S]: ...

    def event[E](self, port: type[E]) -> Ticketed[E]: ...


class ReleaseFacets(Protocol):
    def owned[O](self, port: type[O]) -> Ticketed[O]: ...


# ---- the author-implemented leaf (B1-C1..C4)


class WorkUnit[P](Protocol):
    def declare(self) -> LeafDeclaration: ...

    def observe(self, params: P, reads: ReadFacets, ctx: ObserveContext) -> Observation: ...

    def advance(
        self, params: P, state: Verdict, effects: EffectFacets, ctx: ActContext
    ) -> Step: ...

    def release(
        self, params: P, handle: CreatedHandle, effects: ReleaseFacets, ctx: ActContext
    ) -> Step: ...


# ---- the loop-side record view (V-4.5, MC-B2-02)


@final
@dataclass(frozen=True, slots=True)
class TicketView:
    """V-4 `TicketEntry`'s fields, in V-4's order. `release` is the recorded V-10 descriptor,
    opaque here: the join never reads it."""

    lineage: Lineage
    effect: EffectId
    facet: EffectFacetClass
    attempt: int
    repeat: Repeat
    lifetime: Lifetime
    release: object
    remedy: RemedyGrant | None
    issued_at: Instant
    confirmation: Confirmation | None
    handle: CreatedHandle | None
    result: RecordedResult | None
    released_at: Instant | None
    release_outcome: StableCode | None


@final
@dataclass(frozen=True, slots=True)
class StepView:
    """V-4 `StepEntry`'s fields, in V-4's order."""

    lineage: Lineage
    at: Instant
    kind: StepKind
    code: StableCode
    human_action: HumanAction | None
    resend: Resend | None
    handle: CreatedHandle | None


@final
@dataclass(frozen=True, slots=True)
class NodeRecordView:
    """The node's tickets and steps in this root, in record order (V-4.5)."""

    tickets: tuple[TicketView, ...] = ()
    steps: tuple[StepView, ...] = ()

    def release_set(self) -> tuple[CreatedHandle, ...]:
        """This node's part of the root's release set (V-4.4), in issue order: each
        `CreatedHandle` of a `RUN` ticket not yet recorded released. `FOUND` and `DURABLE` entries
        are not in it by construction. A record query, not a branch of the loop (B1-I1)."""
        return tuple(
            t.handle
            for t in self.tickets
            if t.handle is not None and t.released_at is None and t.lifetime is Lifetime.RUN
        )

    def with_held(self, steps: Iterable[StepView]) -> NodeRecordView:
        """The view with the loop's in-process held steps appended after the durable ones
        (V-4.5, A1c3-1). The durable part is unchanged."""
        return NodeRecordView(tickets=self.tickets, steps=self.steps + tuple(steps))
