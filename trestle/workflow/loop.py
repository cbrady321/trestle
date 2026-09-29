"""The converge loop (L.SV-5.7; B1-C7, B1-C9..C11, B1-E4..E7, B1-O3..O5; MC-24).

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
* The record the join reads is `lane.node_record(path)` plus the steps the lane refused to hold,
  which the loop keeps in process for the life of the root (V-4.5, A1c3-1). Held steps are never
  written to the lane, so the fold and the sweep never see them.

The loop owns the attempt measure (B1-E5): every ADVANCE either issues a ticket, which the lane
bounds by `max_attempts`, or records a step, or ends the node `UNIT_RAISED`. A call that leaves the
record unchanged is a defect that would spin on the same join, so it ends the node `UNIT_RAISED`
too.

Imports stdlib, this package and `trestle.common.plan` only (C.5 step 4); the operator bounds it
needs beyond B2's surface come through `services.FinalizationBounds`.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
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
from trestle.workflow.facets import EffectBinder, FacetContext, ReadBinder
from trestle.workflow.join import join
from trestle.workflow.units import (
    Acted,
    Blocked,
    EffectRefused,
    Failed,
    NoAction,
    NodeRecordView,
    StepView,
)
from trestle.workflow.values import (
    CancelSignal,
    ClockReading,
    Condition,
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

_ESCAPED: object = object()  # `advance` ended in an `EffectRefused` rather than returning

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
        return self.services.evidence()


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
        self.verdict: Verdict | None = None
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
        """Walk to STOP, or until the goal leaves CONVERGE."""
        self.start()
        while self.step():
            pass

    def start(self) -> None:
        """B1-C2: observe once before the first join, then join."""
        self._observe()
        self._rejoin()

    def step(self) -> bool:
        """One `decide` call and the commands it returns, in order (B1-C10). After the last
        command the loop joins again. False when the walk is over: STOP, or the goal is no longer
        CONVERGE."""
        verdict = self.verdict
        if verdict is None or self._loop.goal is not Goal.CONVERGE:
            return False
        for command in decide(self._decl.flags, verdict.condition, Goal.CONVERGE):
            if command is Command.STOP:
                return False
            self._run(command)
            if self._loop.goal is not Goal.CONVERGE:
                break
        self._rejoin()
        return self._loop.goal is Goal.CONVERGE

    def end(self) -> None:
        """Write the vertex's `NodeEnd` when its walk reached STOP (B1-C11): the last verdict's
        condition, code, provenance and, exactly when V-3.7's presence rule requires them, human
        action and re-send; `cut` is None because the node reached this condition itself."""
        verdict = self.verdict
        if verdict is None or not self._reached_stop(verdict):
            return
        self._loop.end_vertex(
            self.path,
            condition=verdict.condition,
            code=verdict.code,
            human_action=verdict.human_action,
            resend=verdict.resend,
            provenance=verdict.provenance,
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

    def _observe(self) -> None:
        """Call the unit's `observe` through read facets only (B1-C2); a raise or a malformed
        observation is UNIT_RAISED (B1-E6), and the previous observation stays."""
        context = _CallContext(self._lineage, self._loop.services)
        try:
            observation = self._unit.observe(self._params, ReadBinder(self._facets(None)), context)
        except Exception as exc:  # noqa: BLE001 (plugin code: any raise is the unit's, B1-E6)
            self._unit_raised(f"observe raised {type(exc).__name__}: {exc}")
            return
        problem = _observation_problem(observation, self._decl)
        if problem is not None:
            self._unit_raised(problem)
            return
        self._observation = observation

    def _poll(self) -> None:
        """POLL under CONVERGE: wait the policy's interval through `CancelSignal.wait`, then
        observe (V-3.5); never `advance` (B1-C10). The join follows the tuple."""
        self._loop.services.cancellation().wait(self._decl.wait.poll_every)
        self._observe()

    # ------------------------------------------------------------------ advance

    def _mark(self) -> tuple[int, int, int]:
        durable = self._loop.lane.node_record(self.path)
        return len(durable.tickets), len(durable.steps), len(self._held)

    def _advance(self) -> None:
        """ADVANCE (B1-C3): call `advance` with the joined state and bind facets for this call.
        A returned `Step` is received (B1-C7, B1-E5); an `EffectRefused` a facet raised was
        recorded by the facet and is never UNIT_RAISED (B1-E4); any other raise is (B1-E6)."""
        verdict = self.verdict
        assert verdict is not None
        before = self._loop.lane.node_record(self.path)
        marked = self._mark()
        self._refusal_seen = False
        context = _CallContext(self._lineage, self._loop.services, verdict.remedy)
        facets = EffectBinder(self._facets(verdict.remedy))
        returned: object = _ESCAPED
        try:
            returned = self._unit.advance(self._params, verdict, facets, context)
        except EffectRefused:
            pass  # the facet recorded the outcome before raising (B1-E4)
        except Exception as exc:  # noqa: BLE001 (plugin code: any raise is the unit's, B1-E6)
            self._unit_raised(f"advance raised {type(exc).__name__}: {exc}")
            return
        if self._loop.goal is not Goal.CONVERGE:
            return  # a facet flipped the goal: the node is in the RELEASE walk (B1-E4)
        if returned is not _ESCAPED:
            issued = self._loop.lane.node_record(self.path).tickets[len(before.tickets) :]
            self._receive(returned, issued, verdict.remedy)
        if self._loop.goal is Goal.CONVERGE and self._mark() == marked:
            # the loop-owned attempt measure: an ADVANCE that leaves the record as it was would
            # repeat the same join forever, so it ends the node instead
            self._unit_raised("an advance that recorded nothing and changed nothing")

    def _receive(self, step: object, issued: tuple[Any, ...], remedy: RemedyGrant | None) -> None:
        """Record the `Step` `advance` returned (B1-C7), after the receipt checks (B1-E5)."""
        if self._refusal_seen:
            # refusal precedence (V-3.7): the facet recorded the outcome; the step is evidence only
            self._evidence(_EVIDENCE_STEP, {"step": type(step).__name__})
            if remedy is not None and not any(t.effect == remedy.effect for t in issued):
                self._unit_raised("a ticketless re-advance under a granted remedy")
            return
        problem = _step_problem(step)
        if problem is not None:
            self._unit_raised(problem)
        elif isinstance(step, Acted):
            if not issued:
                self._unit_raised("Acted from a call that issued no ticket")
        elif isinstance(step, NoAction):
            if self._decl.flags.completion is CompletionSource.RECORDED:
                self._unit_raised("NoAction from the advance of a RECORDED leaf")
            else:
                self._record_step(StepKind.NO_ACTION, step.reason)
        elif isinstance(step, Blocked):
            self._record_step(StepKind.BLOCKED, step.code, step.human_action, step.resend)
        elif isinstance(step, Failed):
            self._evidence(_EVIDENCE_FAILED, {"code": step.code, "detail": step.detail})
            self._record_step(StepKind.FAILED, step.code)

    # ------------------------------------------------------------------ steps and evidence

    def _record_step(
        self,
        kind: StepKind,
        code: StableCode,
        human_action: str | None = None,
        resend: Resend | None = None,
    ) -> None:
        """`record_step`; FULL or UNAVAILABLE keeps the step in process (V-4.5, A1c3-1). Any other
        refusal is a loop defect (B2-C7)."""
        step = StepView(
            self._lineage, self._loop.now(), kind, code, human_action, resend, handle=None
        )
        refused = self._loop.lane.record_step(step)
        if refused is None:
            return
        if refused in (svc.LaneRefusal.FULL, svc.LaneRefusal.UNAVAILABLE):
            self._held.append(step)
            return
        raise RuntimeError(f"the lane refused a loop step: {refused.value}")

    def _unit_raised(self, reason: str) -> None:
        """A contract violation or an uncaught raise: the goal flips first, then the node's
        `StepEntry(FAILED, UNIT_RAISED)` is recorded, so every later entry saw the flip (B1-E6)."""
        self._loop.flip_goal()
        self._evidence(_EVIDENCE_UNIT_RAISED, {"reason": reason})
        self._record_step(StepKind.FAILED, codes.UNIT_RAISED)

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
    ) -> None:
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
        """B1-O3, B1-O4 for one vertex, then the walk (B1-C9)."""
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
        walk.end()

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
        self.lane.record_plan(
            svc.PlanIdentity(
                declaration_digest=plan.declaration_digest or "",
                args_hash=_sha256(canonical_json(dict(self.intent))),
                selection=(),
                observations_digest=_sha256(_NO_OBSERVATIONS),
            )
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
