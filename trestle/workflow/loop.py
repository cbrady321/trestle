"""The converge loop (L.SV-5.7, L.SV-5.8; B1-C7, B1-C9..C11, B1-E4..E7, B1-O3..O8; MC-24).

`run_tree(ctx, entry, intent)` is the plugin callable's one call. It reads B2's run services from
`ctx.run_services`, proves the two admitted digests and the dispatch budget before anything else,
writes the plan identity once, then walks the root. It returns `None`: the outcome is the host's,
projected from the lane (B1-C9, B4-I5); the loop computes no primary and imports no precedence
module.

Shape, so the leaves that extend this file (release pass, goal flips, retries, remedies, waits)
each change one method:

* `Loop` is one root: the proofs, the plan identity, `end_vertex` (the one `NodeEnd` writer, B1-C11)
  and the root's goal (`flip_goal`, idempotent, B1-E6). Nothing below it branches on a resource
  kind, a realization kind, a port kind or a lifetime (B1-I1): the only per-node branch is
  `decide` over the flags, the join's condition and the goal (V-6.1).
* `LeafWalk` is one leaf vertex's walk: `start` observes and joins once, then each `step` is one
  `decide` call and the commands it returns, in order (B1-C10). `converge` runs steps until the walk
  ends. The join is the only producer of a verdict (B1-I2); `advance` and `observe` are the unit's,
  called through facets bound for that call alone (B1-C6).
* `run` writes the vertex's `NodeEnd` when its walk ends, then runs the release pass (B1-O6): for
  each `CreatedHandle` in the node's release set, in reverse issue order, `release` once, then wait
  on a plain clock until the unit observes the selector absent, and only then `record_released`. A
  handle not observed absent stays unrecorded and is the host sweep's (B2-C9).
* The record the join reads is `lane.node_record(path)` plus the steps the lane refused to hold,
  which the loop keeps in process for the life of the root (V-4.5, A1c3-1). Held steps are never
  written to the lane, so the fold and the sweep never see them.

The loop reports every step as structured fields through `RunServices.evidence` (V-13
`EvidenceSink`, B2-C13; L.SL-10.1, WR-EVID-3): `plan.identity` once, then `step.observed`,
`step.postcondition` (the checks and the join's verdict), `step.action`, `step.repair` (a remedy's
ticket) and, in the release pass, `step.cleanup`. They are the runtime's alone: the evidence a unit
gets in its context refuses those kinds. The sink truncates and marks an event over EVENT_MAX and
never raises, so no fact can fail a node.

The loop owns the attempt measure (B1-E5): every ADVANCE either issues a ticket, which the lane
bounds by `max_attempts`, or records a step, or ends the node `UNIT_RAISED`. A call that leaves the
record unchanged is a defect that would spin on the same join, so it ends the node `UNIT_RAISED`
too.

Imports stdlib, this package and `trestle.common.plan` only (C.5 step 4); the operator bounds it
needs beyond B2's surface come through `services.FinalizationBounds`.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from trestle.common.plan import bounds, carving, formats
from trestle.common.plan.declared import DeclaredTree, canonical_json
from trestle.workflow import codes, human_actions
from trestle.workflow import services as svc
from trestle.workflow.decide import Command, decide
from trestle.workflow.declarations import (
    CompletionSource,
    Declaration,
    JsonValue,
    LeafDeclaration,
    StableCode,
    WorkflowEntry,
)
from trestle.workflow.extract import ExtractionRefused, extract_root
from trestle.workflow.facets import (
    EffectBinder,
    FacetContext,
    ReadBinder,
    ReleaseBinder,
    declared_effect,
)
from trestle.workflow.join import join
from trestle.workflow.units import (
    Acted,
    Blocked,
    EffectRefused,
    Failed,
    NoAction,
    NodeRecordView,
    StepView,
    TicketView,
)
from trestle.workflow.values import (
    CancelSignal,
    CheckResult,
    ClockReading,
    Condition,
    CreatedHandle,
    EvidenceSink,
    Goal,
    HostScopeReading,
    Instant,
    Lineage,
    NodePath,
    NodeTerms,
    Observation,
    Provenance,
    RemedyGrant,
    Resend,
    StepKind,
    Verdict,
)

ROOT = NodePath(())

# B1-C7 receipt-check spellings, kept beside the bounds they measure (V-13).
_EVIDENCE_STEP = "loop_step_evidence"
_EVIDENCE_UNIT_RAISED = "loop_unit_raised"
_EVIDENCE_FAILED = "loop_failed_detail"
_EVIDENCE_UNCOVERED = "loop_precondition_uncovered"

_ESCAPED: object = object()  # `advance` ended in an `EffectRefused` rather than returning

# WR-EVID-3 step facts: event kinds only the loop writes (`trestle.child.context.STEP_FACT_KINDS`
# is the same set: the context refuses them from a plugin, tests pin both).
PLAN_IDENTITY = "plan.identity"
STEP_OBSERVED = "step.observed"
STEP_POSTCONDITION = "step.postcondition"
STEP_ACTION = "step.action"
STEP_REPAIR = "step.repair"
STEP_CLEANUP = "step.cleanup"
STEP_FACT_KINDS = frozenset(
    {PLAN_IDENTITY, STEP_OBSERVED, STEP_POSTCONDITION, STEP_ACTION, STEP_REPAIR, STEP_CLEANUP}
)

# The B1-E7 selection observations the root made before its plan identity: none at one leaf.
_NO_OBSERVATIONS = "[]"


class TreeBandError(NotImplementedError):
    """A composite vertex reached the loop: the in-library walk is tree-band work (TM-B2-6, OQ-28).
    Unreachable through admission, which refuses more than one vertex (L.SV-3.5)."""


@dataclass(frozen=True, slots=True)
class Stop:
    """A B1-E7 stop of the root: the condition and code its `NodeEnd` carries."""

    condition: Condition
    code: StableCode


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def plan_path(text: str) -> NodePath:
    """A plan vertex's canonical path text (`""` the root, else `/`-separated) as a `NodePath`."""
    return NodePath(tuple(text.split("/")) if text else ())


def _path_text(path: NodePath) -> str:
    return "/".join(path.segments) or "(root)"


def _presence(condition: Condition | None, code: StableCode | None) -> bool:
    """V-3.7's presence rule: a human action and a re-send iff the condition is BLOCKED or
    INCOMPATIBLE, or the code is POSTCONDITION_TIMEOUT or EFFECT_UNCONFIRMED."""
    return condition in (Condition.BLOCKED, Condition.INCOMPATIBLE) or code in codes.PRESENCE_CODES


class _RootGoal:
    """The root's goal: CONVERGE until any node or facet flips it, then RELEASE for every node at
    once (B1-C10, B1-E6). The flip is idempotent and one-way."""

    __slots__ = ("_goal",)

    def __init__(self) -> None:
        self._goal = Goal.CONVERGE

    @property
    def value(self) -> Goal:
        return self._goal

    def flip(self) -> None:
        self._goal = Goal.RELEASE


class _UnitEvidence:
    """The evidence sink a unit sees: the run's, except that the step-fact kinds are the loop's
    alone and a unit's event of one is dropped (WR-EVID-3)."""

    __slots__ = ("_sink",)

    def __init__(self, sink: EvidenceSink) -> None:
        self._sink = sink

    def event(self, event_kind: str, fields: Mapping[str, JsonValue]) -> None:
        if event_kind not in STEP_FACT_KINDS:
            self._sink.event(event_kind, fields)


@dataclass(frozen=True, slots=True)
class _CallContext:
    """`ObserveContext` / `ActContext` for one unit call (B1-C2, B1-C3): the lineage, the one clock
    read fresh at each access, the cancel signal, the evidence sink, and the remedy the loop
    granted for this call (`advance` only)."""

    lineage: Lineage
    services: svc.RunServices
    remedy: RemedyGrant | None = None

    @property
    def clock(self) -> ClockReading:
        return self.services.clock()

    @property
    def cancellation(self) -> CancelSignal:
        return self.services.cancellation()

    @property
    def evidence(self) -> EvidenceSink:
        return _UnitEvidence(self.services.evidence())


def _value(item: object) -> Any:
    """An enum's value, else the item (a JSON-ready field)."""
    return getattr(item, "value", item)


def _check_fields(result: CheckResult) -> dict[str, JsonValue]:
    return {"satisfied": result.satisfied, "code": result.code, "detail": result.detail}


def _ticket_fields(ticket: TicketView) -> dict[str, JsonValue]:
    """A ticket as the step-action fact reports it: what was issued, how it was answered, and for
    an event effect the recorded result (a test's pass or fail and its counts)."""
    confirmation = ticket.confirmation
    result = ticket.result
    return {
        "effect": ticket.effect,
        "facet": _value(ticket.facet),
        "attempt": ticket.attempt,
        "remedy": None
        if ticket.remedy is None
        else {"code": ticket.remedy.code, "attempt": ticket.remedy.attempt},
        "status": None if confirmation is None else _value(confirmation.status),
        "code": None if confirmation is None else confirmation.code,
        "identity": None if confirmation is None else confirmation.identity,
        "result": None
        if result is None
        else {
            "passed": result.passed,
            "code": result.code,
            "counts": None
            if result.counts is None
            else {
                "passed": result.counts.passed,
                "failed": result.counts.failed,
                "errors": result.counts.errors,
                "skipped": result.counts.skipped,
            },
        },
    }


def _observation_problem(observation: object, declaration: LeafDeclaration) -> str | None:
    """Why an `observe` return is malformed (B1-C2: one `CheckResult` per declared precondition,
    in order, and the postcondition's), or None."""
    if not isinstance(observation, Observation):
        return f"observe returned {type(observation).__name__}, not an Observation"
    declared = tuple(declaration.preconditions)
    returned = tuple(name for name, _ in observation.preconditions)
    if returned != declared:
        return f"observe checked preconditions {returned!r}, the declaration lists {declared!r}"
    return None


def _uncovered_preconditions(observation: object, declaration: LeafDeclaration) -> tuple[str, ...]:
    """The declared preconditions `observation` carries no check for (B1-E1, V-8 L-7): a
    precondition is covered at one vertex only by an inline check the node's own `observe` returns.
    Empty for anything that is not an `Observation` (that is `_observation_problem`'s)."""
    if not isinstance(observation, Observation):
        return ()
    checked = {name for name, _ in observation.preconditions}
    return tuple(name for name in declaration.preconditions if name not in checked)


def _step_problem(step: object) -> str | None:
    """Why a `Step` a unit returned breaks its contract (B1-E5, B1-C7 receipt checks), or None.
    A `Blocked` needs a human action; every code is measured on its encoded bytes against
    CODE_MAX and a human action against HUMAN_ACTION_MAX (V-13), so nothing over a bound is ever
    recorded, truncated or handed to the lane."""
    if isinstance(step, Blocked):
        if not step.human_action:
            return "Blocked without a human action"
        if not isinstance(step.resend, Resend):
            return "Blocked whose resend is not a Resend"
        if bounds.text_bytes(step.human_action) > bounds.HUMAN_ACTION_MAX:
            return "Blocked whose human action exceeds HUMAN_ACTION_MAX"
    elif not isinstance(step, (Acted, Failed, NoAction)):
        return f"a {type(step).__name__} is not a Step"
    code = getattr(step, "code", getattr(step, "reason", None))
    if code is not None and (
        not isinstance(code, str) or bounds.text_bytes(code) > bounds.CODE_MAX
    ):
        return "a step code that is not a StableCode within CODE_MAX"
    return None


def _observed_absent(observation: Observation) -> bool:
    """V-3.8: absent only when the selector is not present and the port could observe."""
    return not observation.selector_present and observation.code is None


class LeafWalk:
    """One leaf vertex's walk (B1-O5 for a leaf). Not thread-safe; the loop walks one leaf at one
    vertex. A later gate that runs leaves concurrently gives each its own `LeafWalk`."""

    def __init__(
        self,
        loop: Loop,
        path: NodePath,
        unit: Any,
        declaration: LeafDeclaration,
        params: Mapping[str, JsonValue],
    ) -> None:
        self._loop = loop
        self.path = path
        self._unit = unit
        self._decl = declaration
        self._params = params
        self._lineage = loop.services.lineage(path)
        self._held: list[StepView] = []  # steps the lane refused to hold, in process (V-4.5)
        self._observation: Observation | None = None
        self._refusal_seen = False  # a facet refused or recorded UNKNOWN in the current call
        self._polls = 0  # CONVERGE polls since the last ADVANCE: the backoff exponent (V-14)
        self.verdict: Verdict | None = None
        self._reached = False  # the walk ended at a condition the node reached itself
        self._terms = NodeTerms(
            flags=declaration.flags,
            retryable=declaration.retryable,
            remedies=declaration.remedies,
            wait=declaration.wait,
            budget=declaration.budget,
            max_attempts=declaration.max_attempts,
            slice_end=loop.services.slice_end(path),
            path=path,
            currency_margin=loop.currency_margin,
        )

    # ------------------------------------------------------------------ the walk

    def converge(self) -> None:
        """Walk to STOP, or until the goal leaves CONVERGE. A stop that is already up starts
        nothing: the node is then NOT_STARTED (B1-C11)."""
        if self._stopped():
            return
        self.start()
        while self.step():
            pass

    def _stopped(self) -> bool:
        """A cancel or the release point (`CancelSignal.requested`, B2-C6) flips the goal to
        RELEASE for every node at once; so has an uncaught raise or a facet's stop (B1-E6)."""
        if self._loop.goal is Goal.CONVERGE and self._loop.services.cancellation().requested:
            self._loop.flip_goal()
        return self._loop.goal is not Goal.CONVERGE

    def start(self) -> None:
        """B1-C2: observe once before the first join, then join. That first observation is also
        the one before any claim, so it is where a declared precondition with no inline check
        stops the node (`_observe(first=True)`, B1-E1, L.SL-7.2)."""
        self._observation = self._observe(first=True) or self._observation
        self._rejoin()

    def step(self) -> bool:
        """One `decide` call and the commands it returns, in order (B1-C10). After the last
        command the loop joins again. False when the walk is over: STOP, or the goal is no longer
        CONVERGE."""
        if self.verdict is None or self._stopped():
            return False
        commands = decide(self._decl.flags, self.verdict.condition, Goal.CONVERGE)
        if commands == (Command.STOP,):
            self._reached = True
            return False
        if self._slice_ended():
            self._carve_exceeded()
            self._reached = True
            return False
        for command in commands:
            self._run(command)
            if self._stopped():
                break
        self._rejoin()
        return self._loop.goal is Goal.CONVERGE

    def _slice_ended(self) -> bool:
        return self._loop.now() >= self._terms.slice_end

    def _carve_exceeded(self) -> None:
        """B1-O8: the slice ended with a non-terminal verdict. A STALE verdict joins once more
        first and yields CURRENCY_UNCONFIRMED (V-3.5); otherwise the loop records
        `StepEntry(FAILED, CARVE_EXCEEDED)` and joins once (J-2)."""
        assert self.verdict is not None
        if self.verdict.condition is Condition.STALE:
            self._rejoin()
            if self._reached_stop(self.verdict):
                return
        self._record_step(StepKind.FAILED, codes.CARVE_EXCEEDED)
        self._rejoin()

    def end(self) -> None:
        """Write the vertex's `NodeEnd` (B1-C11), once. A node that reached its condition itself
        (its walk reached STOP, its slice ended, or its own unit raised) carries its last verdict's
        condition, code, provenance and, exactly when V-3.7's presence rule requires them, human
        action and re-send, `cut` None. A walk the goal cut short is `STOPPED` with its last
        verdict, or `NOT_STARTED` with nothing when no ticket was ever issued."""
        verdict = self.verdict
        own = self._reached or (verdict is not None and verdict.code == codes.UNIT_RAISED)
        if own:
            cut = None
        elif not self._loop.lane.node_record(self.path).tickets:
            self._loop.end_vertex(self.path, condition=None, code=None, cut=svc.Cut.NOT_STARTED)
            return
        else:
            cut = svc.Cut.STOPPED
        assert verdict is not None
        self._loop.end_vertex(
            self.path,
            condition=verdict.condition,
            code=verdict.code,
            human_action=verdict.human_action,
            resend=verdict.resend,
            provenance=verdict.provenance,
            cut=cut,
        )

    def _reached_stop(self, verdict: Verdict) -> bool:
        return decide(self._decl.flags, verdict.condition, Goal.CONVERGE) == (Command.STOP,)

    def _run(self, command: Command) -> None:
        if command is Command.ADVANCE:
            self._advance()
        elif command is Command.POLL:
            self._poll()
        elif command is Command.REJOIN:
            pass  # the join that follows the tuple reads the record without observing
        else:
            raise RuntimeError(f"{command.value} is not a leaf command under CONVERGE")

    # ------------------------------------------------------------------ record and join

    def record(self) -> NodeRecordView:
        """The join's `NodeRecord`: the lane's durable part plus the held steps (V-4.5)."""
        return self._loop.lane.node_record(self.path).with_held(self._held)

    def _rejoin(self) -> None:
        self.verdict = join(
            self._terms,
            self._observation,
            self.record(),
            self._loop.host_scope,
            self._loop.services.clock(),
        )
        self._postcondition_fact(self.verdict)

    def _postcondition_fact(self, verdict: Verdict) -> None:
        """`step.postcondition`: the checks the join read (the last observation's postcondition and
        preconditions) and the verdict it gave, one per join."""
        obs = self._observation
        self._fact(
            STEP_POSTCONDITION,
            {
                "postcondition": None if obs is None else _check_fields(obs.postcondition),
                "preconditions": []
                if obs is None
                else [{"name": name, **_check_fields(r)} for name, r in obs.preconditions],
                "condition": _value(verdict.condition),
                "code": verdict.code,
                "provenance": _value(verdict.provenance),
                "attempts": verdict.attempts.attempts,
            },
        )

    # ------------------------------------------------------------------ observe and poll

    def _facets(self, remedy: RemedyGrant | None) -> FacetContext:
        loop = self._loop
        return FacetContext(
            lane=loop.lane,
            lineage=self._lineage,
            declaration=self._decl,
            ports=loop.ports,
            cancellation=loop.services.cancellation(),
            goal=lambda: loop.goal,
            flip_goal=loop.flip_goal,
            hold=self._held.append,
            now=loop.now,
            remedy=remedy,
            precedence=self._note_refusal,
        )

    def _note_refusal(self) -> None:
        self._refusal_seen = True

    def _observe(
        self, handle: CreatedHandle | None = None, *, first: bool = False
    ) -> Observation | None:
        """Call the unit's `observe` through read facets only (B1-C2). A raise or a malformed
        observation is UNIT_RAISED (B1-E6; from the release pass, that handle's cleanup outcome)
        and gives None. `first` marks the walk's opening observation, the last thing that happens
        before the first claim: a declared precondition it carries no check for stops the node
        there instead (`_stop_uncovered`)."""
        context = _CallContext(self._lineage, self._loop.services)
        try:
            observation = self._unit.observe(self._params, ReadBinder(self._facets(None)), context)
        except Exception as exc:  # noqa: BLE001 (plugin code: any raise is the unit's, B1-E6)
            self._unit_raised(f"observe raised {type(exc).__name__}: {exc}", handle)
            return None
        uncovered = _uncovered_preconditions(observation, self._decl) if first else ()
        if uncovered:
            self._stop_uncovered(uncovered)
            return None
        problem = _observation_problem(observation, self._decl)
        if problem is not None:
            self._unit_raised(problem, handle)
            return None
        assert isinstance(observation, Observation)
        if handle is None:  # a release-pass observation is the cleanup's, reported there
            self._fact(
                STEP_OBSERVED,
                {
                    "present": observation.present,
                    "selector_present": observation.selector_present,
                    "identity_proven": observation.identity_proven,
                    "configuration_compatible": observation.configuration_compatible,
                    "code": observation.code,
                    "found": [
                        {"resource_kind": f.resource_kind, "selector": f.selector}
                        for f in observation.found
                    ],
                    "currency": [
                        {
                            "subject": _value(c.subject),
                            "observed_generation": c.observed_generation,
                            "older": c.older,
                        }
                        for c in observation.currency
                    ],
                },
            )
        return observation

    def _next_interval(self) -> timedelta:
        """V-14: the wait policy's next interval, `poll_every * backoff ** polls`, the exponent
        counted from the last ADVANCE (a new attempt starts a new wait, V-3.1). It never runs past
        `terms.slice_end`: a node is polled at most until its slice ends (J-13a, J-21)."""
        wait = self._decl.wait
        seconds = wait.poll_every.total_seconds() * wait.backoff**self._polls
        left = (self._terms.slice_end - self._loop.now()).total_seconds()
        return timedelta(seconds=max(min(seconds, left), 0.0))

    def _stop_uncovered(self, names: tuple[str, ...]) -> None:
        """B1-E1 at run time, before the first claim: the node ends `FAILED` with
        `PLAN_PRECONDITION_UNCOVERED`, one `StepEntry` and no ticket, so nothing was issued and
        nothing needs releasing. The declaration is what is wrong, so no human action helps (the
        presence rule gives none for `FAILED`); the node reached the condition itself, not the
        goal (B1-C11), and the class the decision table gives it is `FAILED`, never `PASSED`."""
        self._evidence(_EVIDENCE_UNCOVERED, {"preconditions": list(names)})
        self._record_step(StepKind.FAILED, codes.PLAN_PRECONDITION_UNCOVERED)

    def _poll(self) -> None:
        """POLL under CONVERGE: wait the policy's next interval through `CancelSignal.wait`, then
        observe (V-3.5); never `advance` (B1-C10). The join follows the tuple."""
        interval = self._next_interval()
        self._polls += 1
        self._loop.services.cancellation().wait(interval)
        if not self._stopped():
            self._observation = self._observe() or self._observation

    # ------------------------------------------------------------------ advance

    def _cooled_down(self, grant: RemedyGrant) -> bool:
        """The declared remedy's cooldown (V-14): no two tickets of one remedy closer than
        `cooldown`. Waits, through `CancelSignal.wait` and never past the slice's end, until the
        latest remedy ticket for this code is `cooldown` old. False when a stop arrived or the
        slice ended before then (the remedy is not issued)."""
        decl = next((d for d in self._terms.remedies if d.code == grant.code), None)
        previous = next(
            (
                t
                for t in reversed(self._loop.lane.node_record(self.path).tickets)
                if t.remedy is not None and t.remedy.code == grant.code
            ),
            None,
        )
        if decl is None or previous is None:
            return True
        ready = min(previous.issued_at + decl.cooldown, self._terms.slice_end)
        left = ready - self._loop.now()
        if left > timedelta(0):
            self._loop.services.cancellation().wait(left)
        return not self._stopped() and not self._slice_ended()

    def _mark(self) -> tuple[int, int, int]:
        durable = self._loop.lane.node_record(self.path)
        return len(durable.tickets), len(durable.steps), len(self._held)

    def _advance(self) -> None:
        """ADVANCE (B1-C3): call `advance` with the joined state and bind facets for this call.
        A returned `Step` is received (B1-C7, B1-E5); an `EffectRefused` a facet raised was
        recorded by the facet and is never UNIT_RAISED (B1-E4); any other raise is (B1-E6)."""
        verdict = self.verdict
        assert verdict is not None
        if verdict.remedy is not None and not self._cooled_down(verdict.remedy):
            return  # a stop or the slice's end came first: the walk reads it at its next step
        before = self._loop.lane.node_record(self.path)
        marked = self._mark()
        self._refusal_seen = False
        self._polls = 0  # a new attempt starts a new wait
        context = _CallContext(self._lineage, self._loop.services, verdict.remedy)
        facets = EffectBinder(self._facets(verdict.remedy))
        returned: object = _ESCAPED
        try:
            returned = self._unit.advance(self._params, verdict, facets, context)
        except EffectRefused:
            pass  # the facet recorded the outcome before raising (B1-E4)
        except Exception as exc:  # noqa: BLE001 (plugin code: any raise is the unit's, B1-E6)
            self._unit_raised(f"advance raised {type(exc).__name__}: {exc}")
            self._action_fact(before, verdict, "raised")
            return
        if self._loop.goal is not Goal.CONVERGE:
            self._action_fact(before, verdict, returned)
            return  # a facet flipped the goal: the node is in the RELEASE walk (B1-E4)
        if returned is not _ESCAPED:
            issued = self._loop.lane.node_record(self.path).tickets[len(before.tickets) :]
            self._receive(returned, issued, verdict.remedy)
        self._action_fact(before, verdict, returned)
        if self._loop.goal is Goal.CONVERGE and self._mark() == marked:
            # the loop-owned attempt measure: an ADVANCE that leaves the record as it was would
            # repeat the same join forever, so it ends the node instead
            self._unit_raised("an advance that recorded nothing and changed nothing")

    def _action_fact(self, before: NodeRecordView, verdict: Verdict, returned: object) -> None:
        """`step.action` for one ADVANCE: what it issued and how each ticket was answered, what the
        unit returned, and the remedy it ran under; then one `step.repair` for each of its tickets
        that carries a remedy (a repair, V-4 `TicketEntry.remedy`)."""
        issued = self._loop.lane.node_record(self.path).tickets[len(before.tickets) :]
        grant = verdict.remedy
        if returned is _ESCAPED:
            outcome = "refused"  # an EffectRefused the facet recorded (B1-E4)
        elif isinstance(returned, str):
            outcome = returned
        else:
            outcome = type(returned).__name__
        step_code = getattr(returned, "code", getattr(returned, "reason", None))
        self._fact(
            STEP_ACTION,
            {
                "returned": outcome,
                "code": step_code if isinstance(step_code, str) else None,
                "remedy": None
                if grant is None
                else {"code": grant.code, "effect": grant.effect, "attempt": grant.attempt},
                "tickets": [_ticket_fields(t) for t in issued],
            },
        )
        for ticket in issued:
            if ticket.remedy is not None:
                self._fact(STEP_REPAIR, _ticket_fields(ticket))

    def _fact(self, kind: str, fields: dict[str, JsonValue]) -> None:
        """A step fact (WR-EVID-3): structured fields through the run's evidence sink, which
        truncates and marks an over-size event and never raises."""
        self._loop.services.evidence().event(kind, {"path": _path_text(self.path), **fields})

    def _receive(
        self,
        step: object,
        issued: tuple[Any, ...],
        remedy: RemedyGrant | None,
        handle: CreatedHandle | None = None,
    ) -> None:
        """Record the `Step` `advance` (or, with `handle`, `release`) returned (B1-C7), after the
        receipt checks (B1-E5). A step from `release` is that handle's cleanup outcome."""
        if self._refusal_seen:
            # refusal precedence (V-3.7): the facet recorded the outcome; the step is evidence only
            self._evidence(_EVIDENCE_STEP, {"step": type(step).__name__})
            if remedy is not None and not any(t.effect == remedy.effect for t in issued):
                self._unit_raised("a ticketless re-advance under a granted remedy", handle)
            return
        problem = _step_problem(step)
        if problem is not None:
            self._unit_raised(problem, handle)
        elif isinstance(step, Acted):
            if not issued:
                self._unit_raised("Acted from a call that issued no ticket", handle)
        elif isinstance(step, NoAction):
            if self._decl.flags.completion is CompletionSource.RECORDED and handle is None:
                self._unit_raised("NoAction from the advance of a RECORDED leaf", handle)
            else:
                self._record_step(StepKind.NO_ACTION, step.reason, handle=handle)
        elif isinstance(step, Blocked):
            self._record_step(StepKind.BLOCKED, step.code, step.human_action, step.resend, handle)
        elif isinstance(step, Failed):
            self._evidence(_EVIDENCE_FAILED, {"code": step.code, "detail": step.detail})
            self._record_step(StepKind.FAILED, step.code, handle=handle)

    # ------------------------------------------------------------------ release (B1-O6, B1-C10)

    def release_all(self) -> None:
        """The release pass, on RELEASE and on done alike: the goal becomes RELEASE (so a
        non-release effect is refused from here, B1-E4), then `decide` gives `RELEASE_HANDLES`."""
        self._loop.flip_goal()
        condition = self.verdict.condition if self.verdict is not None else Condition.UNSATISFIED
        for command in decide(self._decl.flags, condition, Goal.RELEASE):
            if command is not Command.RELEASE_HANDLES:
                raise RuntimeError(f"{command.value} is not a leaf command under RELEASE")
            self._release_handles()

    def _release_handles(self) -> None:
        """RELEASE_HANDLES (B1-C10): each `CreatedHandle` of this node in the root's release set
        (V-4.4), in reverse issue order. The release set is read from the record, never from an
        observation (V-2.2)."""
        for handle in reversed(self.record().release_set()):
            issued = self._release_call(handle)
            released = self._await_absence(handle)
            self._fact(
                STEP_CLEANUP,
                {
                    "effect": handle.effect,
                    "selector": handle.selector,
                    "released": released,
                    "tickets": [_ticket_fields(t) for t in issued],
                },
            )

    def _release_call(self, handle: CreatedHandle) -> tuple[TicketView, ...]:
        """Call `release` once for `handle` (B1-C4). Whatever it returns, or a facet records, is
        the handle's cleanup outcome (`StepEntry.handle` set), never the node's condition; a raise
        is UNIT_RAISED as a cleanup outcome too (B1-E6). The tickets the call issued are returned
        for the cleanup fact."""
        before = self._loop.lane.node_record(self.path)
        self._refusal_seen = False
        context = _CallContext(self._lineage, self._loop.services)
        facets = ReleaseBinder(self._facets(None), handle)
        try:
            returned = self._unit.release(self._params, handle, facets, context)
        except EffectRefused:
            return self._loop.lane.node_record(self.path).tickets[len(before.tickets) :]
        except Exception as exc:  # noqa: BLE001 (plugin code: any raise is the unit's, B1-E6)
            self._unit_raised(f"release raised {type(exc).__name__}: {exc}", handle)
            return self._loop.lane.node_record(self.path).tickets[len(before.tickets) :]
        issued = self._loop.lane.node_record(self.path).tickets[len(before.tickets) :]
        self._receive(returned, issued, None, handle)
        return issued

    def _await_absence(self, handle: CreatedHandle) -> bool:
        """POLL under RELEASE: wait the policy's interval on a plain clock, ignoring
        cancellation (a cancel that already arrived would end every wait at once), then observe,
        bounded by the effect's `release_timeout` and the root deadline. Only an observed absence
        (V-3.8) writes `record_released`; otherwise the handle stays unrecorded and is the host
        sweep's (B2-C9), never reported released by the loop (WR-OWN-6). True when it was recorded
        released."""
        loop = self._loop
        declared = declared_effect(self._decl, handle.effect)
        timeout = declared.release_timeout if declared is not None else None
        give_up = min(loop.now() + (timeout or timedelta(0)), loop.services.clock().root_deadline)
        while (remaining := give_up - loop.now()) > timedelta(0):
            loop.sleep(min(self._decl.wait.poll_every, remaining).total_seconds())
            observation = self._observe(handle)
            if observation is not None and _observed_absent(observation):
                loop.lane.record_released(handle, None)
                return True
        return False

    # ------------------------------------------------------------------ steps and evidence

    def _record_step(
        self,
        kind: StepKind,
        code: StableCode,
        human_action: str | None = None,
        resend: Resend | None = None,
        handle: CreatedHandle | None = None,
    ) -> None:
        """`record_step`; FULL or UNAVAILABLE keeps the step in process (V-4.5, A1c3-1). Any other
        refusal is a loop defect (B2-C7). `handle` marks a release's cleanup outcome (V-3.7)."""
        step = StepView(self._lineage, self._loop.now(), kind, code, human_action, resend, handle)
        refused = self._loop.lane.record_step(step)
        if refused is None:
            return
        if refused in (svc.LaneRefusal.FULL, svc.LaneRefusal.UNAVAILABLE):
            self._held.append(step)
            return
        raise RuntimeError(f"the lane refused a loop step: {refused.value}")

    def _unit_raised(self, reason: str, handle: CreatedHandle | None = None) -> None:
        """A contract violation or an uncaught raise: the goal flips first, then the node's
        `StepEntry(FAILED, UNIT_RAISED)` is recorded, so every later entry saw the flip (B1-E6)."""
        self._loop.flip_goal()
        self._evidence(_EVIDENCE_UNIT_RAISED, {"reason": reason})
        self._record_step(StepKind.FAILED, codes.UNIT_RAISED, handle=handle)

    def _evidence(self, kind: str, fields: dict[str, JsonValue]) -> None:
        self._loop.services.evidence().event(kind, {"path": _path_text(self.path), **fields})


class Loop:
    """One root run: the proofs and the plan identity that precede every effect, the one
    `NodeEnd` writer, the goal, and the walk of the root."""

    def __init__(
        self,
        services: svc.RunServices,
        entry: WorkflowEntry,
        intent: Mapping[str, JsonValue],
        ports: Mapping[type, object] | None = None,
        *,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.sleep = sleep  # the RELEASE wait is a plain clock wait: it ignores cancellation
        self.services = services
        self.entry = entry
        self.intent = intent
        self.ports: Mapping[type, object] = dict(ports or {})
        self.lane = services.attempts()
        self.host_scope = HostScopeReading(())  # no host section is declared at one vertex (B2-C8)
        reserve_s, margin_s = 0.0, 0.0
        if isinstance(services, svc.FinalizationBounds):
            reserve_s, margin_s = services.finalization_reserve_s, services.currency_margin_s
        self._reserve_s = reserve_s
        self.currency_margin = timedelta(seconds=margin_s)
        self._goal = _RootGoal()

    @property
    def goal(self) -> Goal:
        return self._goal.value

    def flip_goal(self) -> None:
        self._goal.flip()

    def now(self) -> Instant:
        return self.services.clock().now

    # ------------------------------------------------------------------ the root

    def run(self) -> None:
        """B1-O3, B1-O4 for one vertex, the walk, the vertex's `NodeEnd`, then the release pass
        (B1-C9): every vertex has its end before anything is given back."""
        plan = self.services.admitted().accepted
        declaration, tree = self._derive()
        stop = self._proof_stop(plan, tree.digest if tree is not None else None)
        self._record_plan(plan)
        if stop is not None:
            self._end_root_stop(stop)
            return
        if not isinstance(declaration, LeafDeclaration):
            raise TreeBandError("walk_children: tree band")
        unit = self.entry.units[self.entry.root]
        walk = LeafWalk(self, ROOT, unit, declaration, self.intent)
        walk.converge()
        walk.end()  # before the release pass: the run has "ended" once every vertex has its end
        walk.release_all()

    def _derive(self) -> tuple[Declaration | None, DeclaredTree | None]:
        """B1-O3: re-derive the declaration in-run. A `declare()` that no longer yields a
        declaration is a declaration that is not the admitted one."""
        try:
            return extract_root(self.entry)
        except ExtractionRefused:
            return None, None

    def _proof_stop(self, plan: svc.PlanAccepted, derived_digest: str | None) -> Stop | None:
        """The B1-E7 stops that need no effect: each digest alone (B2-C3), then the dispatch
        re-check (B2-C5: `root_deadline - now < worst_case + release_slice`)."""
        stale = Stop(Condition.FAILED, codes.DECLARATION_STALE)
        if derived_digest is None or derived_digest != plan.declaration_digest:
            return stale
        if plan.plan_digest is None or formats.plan_digest(plan.body()) != plan.plan_digest:
            return stale
        clock = self.services.clock()
        need = carving.worst_case_s(plan, self._reserve_s) + plan.release_slice
        if clock.root_deadline - clock.now < timedelta(seconds=need):
            return Stop(Condition.BLOCKED, codes.BUDGET_DOES_NOT_FIT)
        return None

    def _record_plan(self, plan: svc.PlanAccepted) -> None:
        """The one plan entry, before the first `issue` and the first `record_end`, on every path
        including a stop (B1-C11, B1-I8); it reserves each vertex's `NodeEnd` slot (B2-C7)."""
        identity = svc.PlanIdentity(
            declaration_digest=plan.declaration_digest or "",
            args_hash=_sha256(canonical_json(dict(self.intent))),
            selection=(),
            observations_digest=_sha256(_NO_OBSERVATIONS),
        )
        self.lane.record_plan(identity)
        self.services.evidence().event(  # WR-EVID-3: the plan's identity, once, as fields
            PLAN_IDENTITY,
            {
                "plan_digest": plan.plan_digest,
                "declaration_digest": identity.declaration_digest,
                "args_hash": identity.args_hash,
                "selection": [],
                "observations_digest": identity.observations_digest,
            },
        )

    def _end_root_stop(self, stop: Stop) -> None:
        human_action: str | None = None
        resend: Resend | None = None
        if _presence(stop.condition, stop.code):
            human_action, resend = human_actions.render(
                stop.code, path=_path_text(ROOT), effect="", subject=""
            )
        self.end_vertex(
            ROOT,
            condition=stop.condition,
            code=stop.code,
            human_action=human_action,
            resend=resend,
        )
        self.end_unstarted_but(ROOT)

    def end_unstarted_but(self, *ended: NodePath) -> None:
        """Every vertex of `V_run` that has no `NodeEnd` yet is written `cut=NOT_STARTED` with no
        condition (B1-C11, B1-O7): a stop before the walk (B1-E7) started nothing, so each vertex
        below the root is one. `ended` are the paths already written. Plan order, root first."""
        done = set(ended)
        for vertex in self.services.admitted().accepted.vertices:
            path = plan_path(vertex.path)
            if path not in done:
                self.end_vertex(path, condition=None, code=None, cut=svc.Cut.NOT_STARTED)

    # ------------------------------------------------------------------ B1-C11

    def end_vertex(
        self,
        path: NodePath,
        *,
        condition: Condition | None,
        code: StableCode | None,
        human_action: str | None = None,
        resend: Resend | None = None,
        provenance: Provenance | None = None,
        cut: svc.Cut | None = None,
    ) -> None:
        """The one `NodeEnd` writer: exactly one per vertex, through `AttemptLane.record_end` into
        the slot `record_plan` reserved. `UNAVAILABLE` leaves the vertex unended, and the host
        answers `VERTEX_UNENDED` (B4-C2 rule (4)); any other refusal is a loop defect (B2-C7)."""
        refused = self.lane.record_end(
            svc.NodeEnd(
                lineage=self.services.lineage(path),
                at=self.now(),
                condition=condition,
                code=code,
                human_action=human_action,
                resend=resend,
                provenance=provenance,
                cut=cut,
            )
        )
        if refused is not None and refused is not svc.LaneRefusal.UNAVAILABLE:
            raise RuntimeError(f"the lane refused a NodeEnd: {refused.value}")


def run_tree(
    ctx: svc.RunContext,
    entry: WorkflowEntry,
    intent: Mapping[str, JsonValue],
    *,
    ports: Mapping[type, object] | None = None,
) -> None:
    """B1-C9. `ports` maps each port protocol to its one implementation (B1-C6 facet binding); the
    plugin callable that hands `run_tree` its entry hands it these too (a plan gap: B1-C9 names no
    other route)."""
    Loop(ctx.run_services, entry, intent, ports).run()
