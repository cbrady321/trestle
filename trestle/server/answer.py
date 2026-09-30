"""The host answer (L.SV-4.2; Boundary 4, B4-C1..C8): `project` is a pure projection over the
durable inputs U2 wrote before the terminal row and implements B4 without restating it.

The inputs are the folded lane (`fold.fold_lane`, the only route to the lane, so this module
imports no lane codec), the sweep's `CleanupDisposition`, the `GroupStop`, `spec.plan`'s admitted
plan (None for a plan-less root, B2-C1), `recovered`, and the ledger's error record as a V-11.3
`ExecutionErrorAnswer`. The rule that decides is B4-C2's, applied in order (1), (2), (5), (3), (4);
the primary is B4-C4's key-min candidate through `trestle.common.plan.precedence` (L.SV-4.1), or
the root's own account where B4-C2 says so. The bound and the wire layout are B4-C6's and the
answer HLD's: `to_wire` encodes the decisive fields first (never truncated) and fills `listed`
then `unconfirmed` in key order up to the budget, the rest behind one `detail` handle.

Nothing here reads a file or a clock; the same inputs give the same `TerminalAnswer` and the same
bytes, so the run view recomputes it at read time and `differ d5` compares that with the answer U2
persisted at finalization (`evidence/answer.json`, the full unbudgeted encoding `detail` names).
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path as FsPath
from typing import Any

from trestle.common import codes
from trestle.common.fsutil import atomic_write_json
from trestle.common.outcome import OutcomeClass
from trestle.common.plan import bounds
from trestle.common.plan import precedence as prec
from trestle.common.plan import vocabulary as vocab
from trestle.common.plan.compiler import AdmittedPlan
from trestle.common.plan.vocabulary import (
    Listing,
    NodeClass,
    ResourceDisposition,
    RootStop,
)
from trestle.server import fold, sweep
from trestle.server.sweep import (
    GROUP_TARGET,
    CleanupDisposition,
    GroupLike,
    SweepTarget,
)

Path = tuple[str, ...]

ROOT: Path = ()
ANSWER_FILE = "answer.json"


# ---- B4's answer types


@dataclass(frozen=True, slots=True)
class NodeAnswer:
    path: Path
    listing: Listing
    node_class: NodeClass | None  # set iff listing is CANDIDATE or ROLLED_UP
    condition: str | None  # NodeEnd.condition (the last verdict, for STOPPED)
    code: str | None
    human_action: str | None  # byte-identical to its NodeEnd's (B4-C6)
    resend: str | None
    disposition: ResourceDisposition | None


@dataclass(frozen=True, slots=True)
class UnconfirmedEffect:
    path: Path
    effect: str
    attempt: int


@dataclass(frozen=True, slots=True)
class CleanupAnswer:
    clean: bool
    released: int
    nothing_created: int
    unknown: int
    left_durable: int
    group_confirmed_gone: bool
    helpers_disclosed: bool
    lease_ended_unconfirmed: bool


@dataclass(frozen=True, slots=True)
class ExecutionErrorAnswer:
    """V-11.3: a V-11 code, where it happened (a node path or a phase name), a bounded message."""

    code: str
    phase: str
    message: str


@dataclass(frozen=True, slots=True)
class TestCounts:
    __test__ = False  # not a pytest class
    passed: int
    failed: int
    errors: int
    skipped: int


@dataclass(frozen=True, slots=True)
class TerminalAnswer:
    outcome: OutcomeClass
    root_stop: RootStop | None
    recovered: bool
    primary: NodeAnswer
    listed: tuple[NodeAnswer, ...]
    unconfirmed: tuple[UnconfirmedEffect, ...]
    cleanup: CleanupAnswer
    test_counts: TestCounts | None
    error: ExecutionErrorAnswer | None
    incomplete: Path | None
    detail: str | None


EXHAUSTED_AS: OutcomeClass = prec.EXHAUSTED_AS


def detail_handle(run_id: str) -> str:
    """`<run_id>/answer`, following today's `<run_id>/result` (the answer HLD's choice)."""
    return f"{run_id}/answer"


# ---- the error the host hands over (B2-C10's one terminal-kind -> code map)

_UNENCODABLE_CODES = frozenset({vocab.RESULT_UNENCODABLE, codes.EXECUTION_RESULT_UNENCODABLE})


def bounded_message(text: str) -> str:
    """`message` (V-11.3) within TEXT_MAX bytes, cut on a character boundary (WR-EVID-7)."""
    encoded = text.encode("utf-8")
    if len(encoded) <= bounds.TEXT_MAX:
        return text
    return encoded[: bounds.TEXT_MAX].decode("utf-8", errors="ignore")


def execution_error(
    terminal_kind: str, error_record: Mapping[str, Any] | None
) -> ExecutionErrorAnswer | None:
    """U2's `ExecutionErrorAnswer` for a run that ended in `terminal_kind` (B2-C10, stated once):
    `failed` (the plugin raised) -> UNIT_RAISED; a result the record cannot encode ->
    RESULT_UNENCODABLE; `interrupted` -> EXECUTION_RESTART (B2-C11); `worker_exit`, and any other
    kind that ends the plugin process without a normal return -> WORKER_EXIT. A normal return
    (`succeeded`) and a kind a stop row explains (`cancelled`, `timed_out`) hand over None. A
    failure the child recorded under one of the execution vocabulary's other codes keeps it."""
    if terminal_kind in ("succeeded", "cancelled", "timed_out"):
        return None
    row = error_record or {}
    recorded = row.get("code")
    phase = str(row.get("phase") or "exit")
    message = bounded_message(str(row.get("message") or ""))
    if terminal_kind == "interrupted":
        code = vocab.EXECUTION_RESTART
    elif terminal_kind == "failed":
        if recorded in _UNENCODABLE_CODES:
            code = vocab.RESULT_UNENCODABLE
        elif (
            isinstance(recorded, str)
            and recorded in _PASS_THROUGH
            and (recorded != codes.EXECUTION_PLUGIN_RAISED)
        ):
            code = recorded
        else:
            code = vocab.UNIT_RAISED
    else:
        code = vocab.WORKER_EXIT
    return ExecutionErrorAnswer(code=code, phase=phase, message=message)


_PASS_THROUGH: frozenset[str] = (codes.EXECUTION_CODES | vocab.SINGLE_LEVEL_CODES) - frozenset(
    {codes.EXECUTION_CANCELLED, codes.EXECUTION_DEADLINE_EXCEEDED}
)


# ---- the projection


def _path(text: str) -> Path:
    return tuple(text.split("/")) if text else ROOT


def _spath(path: Path) -> str:
    return "/".join(path)


class _Run:
    """The durable inputs of one run, indexed once."""

    def __init__(self, folded: fold.FoldedRecord, plan: AdmittedPlan | None) -> None:
        self.folded = folded
        self.plan = plan
        self.ends: dict[Path, Any] = {e.lineage.path: e for e in folded.ends}
        self.tickets: dict[Path, list[Any]] = {}
        for ticket in folded.entries:
            self.tickets.setdefault(ticket.lineage.path, []).append(ticket)
        self.entries: set[Path] = set(self.tickets) | {s.lineage.path for s in folded.steps}
        if plan is None:
            self.vrun: frozenset[Path] = frozenset({ROOT})
            self.ordinal: dict[Path, int] = {ROOT: 0}
            self.children: dict[Path, tuple[Path, ...]] = {}
        else:
            self.vrun = fold.walked_paths(folded, plan)
            self.ordinal = {_path(p): n for p, n in plan.precedence_ordinal.items()}
            self.children = {
                _path(v.path): tuple(_path(c) for c in v.children) for v in plan.vertices
            }

    def listing(self, path: Path) -> Listing:
        return prec.listing(self.ends.get(path), path in self.entries)

    def candidate(self, path: Path) -> prec.Candidate | None:
        if self.listing(path) is not Listing.CANDIDATE:
            return None
        end = self.ends[path]
        klass = prec.node_class(end, self.tickets.get(path, ()))
        if klass is None:
            return None
        return prec.Candidate(_spath(path), klass, end.code, self.ordinal.get(path, 0))

    def candidates(self, under: Path | None = None) -> list[prec.Candidate]:
        found = []
        for path in self.vrun:
            if under is not None and (path == under or path[: len(under)] != under):
                continue
            cand = self.candidate(path)
            if cand is not None:
                found.append(cand)
        return found

    def satisfied(self, path: Path) -> bool:
        """Whether a selected child ended satisfied (a leaf's own condition, or a composite that
        rolled up to a passing class)."""
        end = self.ends.get(path)
        if end is None or end.cut is not None:
            return False
        if end.condition is not None:
            return bool(end.condition.value == "satisfied")
        klass = prec.roll_up(
            self.candidates(path), all(self.satisfied(c) for c in self.children.get(path, ()))
        )
        return klass in (NodeClass.PASSED, NodeClass.REPAIRED)

    def node(self, path: Path) -> NodeAnswer:
        """The vertex's account as B4-C3/B4-C8 project it; the same for the root answer's `listed`
        entry and the vertex's child view (B4-I4)."""
        end = self.ends.get(path)
        listing = self.listing(path)
        tickets = self.tickets.get(path, ())
        klass: NodeClass | None = None
        code = end.code if end is not None else None
        human_action = end.human_action if end is not None else None
        resend = end.resend.value if end is not None and end.resend is not None else None
        if listing is Listing.CANDIDATE and end is not None:
            klass = prec.node_class(end, tickets)
        elif listing is Listing.ROLLED_UP:
            inside = self.candidates(path)
            children_ok = all(self.satisfied(c) for c in self.children.get(path, ()))
            klass = prec.roll_up(inside, children_ok)
            best = prec.primary(inside)
            if best is not None:  # the key-min candidate's code, human action and resend
                best_end = self.ends[_path(best.path)]
                code = best_end.code
                human_action = best_end.human_action
                resend = best_end.resend.value if best_end.resend is not None else None
        disposition = None
        if end is not None and klass in (NodeClass.PASSED, NodeClass.REPAIRED):
            disposition = prec.resource_disposition(end, tickets)
        return NodeAnswer(
            path=path,
            listing=listing,
            node_class=klass,
            condition=end.condition.value if end is not None and end.condition else None,
            code=code,
            human_action=human_action,
            resend=resend,
            disposition=disposition,
        )

    def lowest_ordinal(self, listings: Iterable[Listing]) -> Path | None:
        wanted = set(listings)
        paths = [p for p in self.vrun if self.listing(p) in wanted]
        return min(paths, key=lambda p: (self.ordinal.get(p, 0), p), default=None)


def _key_tuple(run: _Run, node: NodeAnswer) -> tuple[int, ...]:
    """A total order over `listed`: group first, then the B4-C4 key / the ordinal, then the path."""
    ordinal = run.ordinal.get(node.path, 0)
    if node.listing is Listing.CANDIDATE and node.node_class is not None:
        end = run.ends[node.path]
        cand = prec.Candidate(_spath(node.path), node.node_class, end.code, ordinal)
        return (0, *prec.key(cand))
    group = {
        Listing.ROLLED_UP: 1,
        Listing.STOPPED: 2,
        Listing.UNENDED: 3,
        Listing.NOT_STARTED: 4,
    }[node.listing]
    return (group, 0, 0, ordinal)


def _unconfirmed(run: _Run) -> tuple[UnconfirmedEffect, ...]:
    """Every ONCE entry of `FoldedRecord.unconfirmed` (B4-C5): by ordinal, effect, attempt."""
    once = [t for t in run.folded.unconfirmed if t.repeat.value == "once"]
    once.sort(key=lambda t: (run.ordinal.get(t.lineage.path, 0), t.effect, t.attempt))
    return tuple(UnconfirmedEffect(t.lineage.path, t.effect, t.attempt) for t in once)


def _cleanup(
    run: _Run,
    cleanup: CleanupDisposition,
    group: GroupLike,
    plan: AdmittedPlan | None,
    leased: bool | None = None,
) -> CleanupAnswer:
    """B4-C7: the handles the loop recorded released, and the sweep's dispositions, one per
    `SweepTarget` with `unknown` dominating; `clean` only as B2-C9 Post allows."""
    disposition: dict[SweepTarget, str] = {}
    for ticket in run.folded.entries:
        if ticket.released_at is not None:
            disposition[SweepTarget(ticket.lineage.path, ticket.effect)] = "released"
    swept: list[tuple[str, tuple[SweepTarget, ...]]] = [
        ("released", cleanup.released),
        ("nothing_created", cleanup.nothing_created),
        ("left_durable", tuple(t for t, _ in cleanup.left_durable)),
        ("unknown", cleanup.unknown),
    ]
    for label, targets in swept:  # `unknown` is last, so it dominates any other disposition
        for target in targets:
            disposition[target] = label
    counts = {"released": 0, "nothing_created": 0, "unknown": 0, "left_durable": 0}
    for label in disposition.values():
        counts[label] += 1
    helpers = any(
        isinstance(t.release, fold.InRunGroup)
        and t.release.helpers_disclosed
        and t.released_at is None
        and (t.confirmation is None or t.confirmation.status != fold.ConfirmationStatus.NOT_APPLIED)
        for t in run.folded.entries
    )
    if leased is None:
        leased = plan is not None and bool(plan.lease_set)
    return CleanupAnswer(
        clean=counts["unknown"] == 0 and not fold.cleanup_is_unknown(run.folded),
        released=counts["released"],
        nothing_created=counts["nothing_created"],
        unknown=counts["unknown"],
        left_durable=counts["left_durable"],
        group_confirmed_gone=bool(group.confirmed_gone),
        helpers_disclosed=helpers,
        lease_ended_unconfirmed=leased and not group.confirmed_gone,
    )


def _test_counts(run: _Run, primary: NodeAnswer) -> TestCounts | None:
    """The primary node's recorded counts, when it is an event with counts."""
    for ticket in reversed(run.tickets.get(primary.path, [])):
        result = ticket.result
        if result is not None and result.counts is not None:
            c = result.counts
            return TestCounts(c.passed, c.failed, c.errors, c.skipped)
    return None


def _planless(
    run: _Run, error: ExecutionErrorAnswer | None, terminal_kind: str | None
) -> tuple[OutcomeClass, NodeAnswer, ExecutionErrorAnswer | None]:
    """B4-T4 (rule (5)): the terminal kind follows from `error.code`; stops and a restart are
    decided by rules (1) and (2) before this, so what reaches here ended by itself: a normal
    return, the plugin raising, the worker exiting, or a result the record could not encode. A
    core run written before stop rows existed answers a cancel or a deadline by its terminal kind
    (or its recorded code)."""
    if error is None and terminal_kind == "cancelled":
        return OutcomeClass.CANCELLED, _stopped_root(), None
    if error is None and terminal_kind == "timed_out":
        return OutcomeClass.TIMED_OUT, _stopped_root(), None
    if error is None:
        primary = NodeAnswer(
            ROOT, Listing.CANDIDATE, NodeClass.PASSED, None, None, None, None, None
        )
        return OutcomeClass.PASSED, primary, None
    if error.code == codes.EXECUTION_CANCELLED:
        return OutcomeClass.CANCELLED, _stopped_root(), None
    if error.code == codes.EXECUTION_DEADLINE_EXCEEDED:
        return OutcomeClass.TIMED_OUT, _stopped_root(), None
    klass = (
        NodeClass.UNENCODABLE_RESULT
        if error.code in _UNENCODABLE_CODES
        else NodeClass.EXECUTION_ERROR
    )
    primary = NodeAnswer(ROOT, Listing.CANDIDATE, klass, None, error.code, None, None, None)
    return OutcomeClass.EXECUTION_ERROR, primary, error


def _stopped_root() -> NodeAnswer:
    return NodeAnswer(ROOT, Listing.STOPPED, None, None, None, None, None, None)


def project(
    folded: fold.FoldedRecord,
    cleanup: CleanupDisposition,
    group: GroupLike,
    plan: AdmittedPlan | None,
    recovered: bool,
    error: ExecutionErrorAnswer | None,
    *,
    summary_budget: int = bounds.SUMMARY_BUDGET_DEFAULT,
    terminal_kind: str | None = None,
    leased: bool | None = None,
) -> TerminalAnswer:
    """B4-C1. `plan` is None only for a plan-less root (B2-C1). `summary_budget` is the run's own
    (the snapshot's `summary_budget`, CL-B1); it decides only whether `detail` is set for an
    overflow, never what is in the decisive fields. `leased` says the run held an environment
    lease (`CleanupAnswer.lease_ended_unconfirmed`, B4-C7): by default the plan's `lease_set`
    says so; a plain plugin that names an environment is answered plan-less yet holds one."""
    run = _Run(folded, plan)
    handle = detail_handle(folded.root)
    root_stop: RootStop | None = None
    incomplete: Path | None = None
    result_error: ExecutionErrorAnswer | None = None
    outcome: OutcomeClass

    def own_account() -> NodeAnswer:
        """The root's own account: its `NodeAnswer`; without a `NodeEnd`, UNENDED if it has
        entries else NOT_STARTED (B4-C2)."""
        if plan is None:
            return _stopped_root()
        return run.node(ROOT)

    stop = folded.stop_rows[0] if folded.stop_rows else None
    if stop is not None:  # rule (1): the first stop row's cause decides, whatever else was seen
        if stop.cause == fold.CAUSE_CANCEL:
            outcome, root_stop = OutcomeClass.CANCELLED, RootStop.CANCEL
        else:
            outcome, root_stop = OutcomeClass.TIMED_OUT, RootStop.RELEASE_POINT
        primary = own_account()
        if outcome is OutcomeClass.TIMED_OUT:
            if plan is None:
                incomplete = ROOT
            else:
                stage = run.lowest_ordinal((Listing.STOPPED, Listing.UNENDED))
                incomplete = stage if stage is not None else primary.path
    elif recovered and not (plan is not None and folded.ended):
        # rule (2): a restart with no stop row. A crash that follows every `NodeEnd` (P4-4 (a),
        # B4-C2's confirmed S2 consequence) is answered by the key (rule (3)): the run had ended,
        # only its terminal row was missing, while `RunView.error` still shows the crash record
        outcome, root_stop = OutcomeClass.EXECUTION_ERROR, RootStop.RESTART
        result_error = error or ExecutionErrorAnswer(
            vocab.EXECUTION_RESTART, "recovery", "the service restarted while the run was in flight"
        )
        primary = own_account()
    elif plan is None:  # rule (5): B4-T4
        outcome, primary, result_error = _planless(run, error, terminal_kind)
        if outcome is OutcomeClass.CANCELLED:
            root_stop = RootStop.CANCEL
        elif outcome is OutcomeClass.TIMED_OUT:
            root_stop, incomplete = RootStop.RELEASE_POINT, ROOT
    elif folded.ended:  # rule (3): the key decides
        outcome, primary, result_error, incomplete = _keyed(run, handle)
    else:  # rule (4): the plugin ended before every vertex did
        outcome = OutcomeClass.EXECUTION_ERROR
        cands = run.candidates()
        best = prec.primary(cands)
        primary = run.node(_path(best.path)) if best is not None else own_account()
        if error is not None and error.code in (vocab.WORKER_EXIT, vocab.UNIT_RAISED):
            result_error = error
        else:
            unended = run.lowest_ordinal((Listing.UNENDED,)) or run.lowest_ordinal(
                (Listing.NOT_STARTED,)
            )
            phase = _spath(unended) if unended is not None else ""
            result_error = ExecutionErrorAnswer(
                vocab.VERTEX_UNENDED,
                phase,
                bounded_message("the plugin ended before every vertex reached a condition"),
            )

    listed_nodes = [run.node(p) for p in run.vrun if p != primary.path]
    listed_nodes.sort(key=lambda n: (_key_tuple(run, n), n.path))
    listed = tuple(listed_nodes) if plan is not None else ()
    unconfirmed = _unconfirmed(run)
    answer = TerminalAnswer(
        outcome=outcome,
        root_stop=root_stop,
        recovered=recovered,
        primary=primary,
        listed=listed,
        unconfirmed=unconfirmed,
        cleanup=_cleanup(run, cleanup, group, plan, leased),
        test_counts=_test_counts(run, primary),
        error=result_error if outcome is OutcomeClass.EXECUTION_ERROR else None,
        incomplete=incomplete if outcome is OutcomeClass.TIMED_OUT else None,
        detail=None,
    )
    overflowed = _inline_lengths(answer, summary_budget) != (len(listed), len(unconfirmed))
    needs_detail = outcome is OutcomeClass.FAILED or overflowed
    return replace_detail(answer, handle if needs_detail else None)


def replace_detail(answer: TerminalAnswer, detail: str | None) -> TerminalAnswer:
    return TerminalAnswer(
        outcome=answer.outcome,
        root_stop=answer.root_stop,
        recovered=answer.recovered,
        primary=answer.primary,
        listed=answer.listed,
        unconfirmed=answer.unconfirmed,
        cleanup=answer.cleanup,
        test_counts=answer.test_counts,
        error=answer.error,
        incomplete=answer.incomplete,
        detail=detail,
    )


def _keyed(
    run: _Run, handle: str
) -> tuple[OutcomeClass, NodeAnswer, ExecutionErrorAnswer | None, Path | None]:
    """Rule (3): no stop row and `ended`: the key decides (B4-C4), the outcome is B4-T3's."""
    best = prec.primary(run.candidates())
    root_children = run.children.get(ROOT, ())
    if best is None:  # no candidate: a B1-C11 defect unless there is no leaf at all
        primary = run.node(ROOT)
        if all(run.satisfied(c) for c in root_children):
            return OutcomeClass.PASSED, primary, None, None
        undecided = ExecutionErrorAnswer(
            vocab.VERTEX_UNENDED, "", bounded_message("no vertex reached a condition")
        )
        return OutcomeClass.EXECUTION_ERROR, primary, undecided, None
    outcome = prec.outcome_of(best.node_class)
    if best.node_class is NodeClass.PASSED:  # a success's primary is the root, rolled up
        root = run.node(ROOT)
        primary = NodeAnswer(
            ROOT,
            Listing.ROLLED_UP,
            NodeClass.PASSED,
            None,
            None,
            None,
            None,
            root.disposition,
        )
    else:
        primary = run.node(_path(best.path))
    error: ExecutionErrorAnswer | None = None
    incomplete: Path | None = None
    if outcome is OutcomeClass.EXECUTION_ERROR:
        error = ExecutionErrorAnswer(
            code=primary.code or vocab.VERTEX_UNENDED,
            phase=_spath(primary.path),
            message=bounded_message(handle),
        )
    if outcome is OutcomeClass.TIMED_OUT:
        stage = run.lowest_ordinal((Listing.STOPPED, Listing.UNENDED))
        incomplete = stage if stage is not None else primary.path
    return outcome, primary, error, incomplete


# ---- the wire encoding (the answer HLD's layout; B4-C6's bound)


def _node_wire(node: NodeAnswer) -> dict[str, Any]:
    return {
        "path": list(node.path),
        "listing": node.listing.value,
        "node_class": node.node_class.value if node.node_class is not None else None,
        "condition": node.condition,
        "code": node.code,
        "human_action": node.human_action,
        "resend": node.resend,
        "disposition": node.disposition.value if node.disposition is not None else None,
    }


def _decisive(answer: TerminalAnswer, detail: str | None) -> dict[str, Any]:
    counts = answer.test_counts
    return {
        "outcome": answer.outcome.value,
        "root_stop": answer.root_stop.value if answer.root_stop is not None else None,
        "recovered": answer.recovered,
        "primary": _node_wire(answer.primary),
        "incomplete": list(answer.incomplete) if answer.incomplete is not None else None,
        "error": (
            {
                "code": answer.error.code,
                "phase": answer.error.phase,
                "message": answer.error.message,
            }
            if answer.error is not None
            else None
        ),
        "cleanup": {
            "clean": answer.cleanup.clean,
            "released": answer.cleanup.released,
            "nothing_created": answer.cleanup.nothing_created,
            "unknown": answer.cleanup.unknown,
            "left_durable": answer.cleanup.left_durable,
            "group_confirmed_gone": answer.cleanup.group_confirmed_gone,
            "helpers_disclosed": answer.cleanup.helpers_disclosed,
            "lease_ended_unconfirmed": answer.cleanup.lease_ended_unconfirmed,
        },
        "test_counts": (
            {
                "passed": counts.passed,
                "failed": counts.failed,
                "errors": counts.errors,
                "skipped": counts.skipped,
            }
            if counts is not None
            else None
        ),
        "detail": detail,
        "listed_count": len(answer.listed),
        "unconfirmed_count": len(answer.unconfirmed),
    }


def _unconfirmed_wire(entry: UnconfirmedEffect) -> dict[str, Any]:
    return {"path": list(entry.path), "effect": entry.effect, "attempt": entry.attempt}


def _size(value: Any) -> int:
    return len(json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


# `detail` is reserved at its V-13 bound in every answer (so setting it never breaks the budget,
# and the fill does not depend on a run id's length): TOKEN_MAX bytes.
_RESERVED_DETAIL = "d" * bounds.TOKEN_MAX


def _inline_lengths(answer: TerminalAnswer, budget: int) -> tuple[int, int]:
    """How many `listed` and `unconfirmed` entries fit inline: the decisive fields (with `detail`
    reserved) are counted first and never truncated, then `listed` and `unconfirmed` fill in
    order, each a prefix, an entry never split, stopping at the first that does not fit."""
    used = _size({**_decisive(answer, _RESERVED_DETAIL), "listed": [], "unconfirmed": []})
    listed = 0
    for node in answer.listed:
        cost = _size(_node_wire(node)) + 1
        if used + cost > budget:
            break
        used += cost
        listed += 1
    unconfirmed = 0
    if listed == len(answer.listed):
        for entry in answer.unconfirmed:
            cost = _size(_unconfirmed_wire(entry)) + 1
            if used + cost > budget:
                break
            used += cost
            unconfirmed += 1
    return listed, unconfirmed


def to_wire(
    answer: TerminalAnswer, summary_budget: int = bounds.SUMMARY_BUDGET_DEFAULT
) -> dict[str, Any]:
    """The `answer` key of the run view (the answer HLD's layout): B4's field names verbatim, the
    decisive fields always present (`null`, never omitted), then `listed_count` and
    `unconfirmed_count` (the full lengths), then `listed` and `unconfirmed` filled in B4's order
    up to `summary_budget`; what does not fit is behind `detail`."""
    listed_n, unconfirmed_n = _inline_lengths(answer, summary_budget)
    body = _decisive(answer, answer.detail)
    body["listed"] = [_node_wire(n) for n in answer.listed[:listed_n]]
    body["unconfirmed"] = [_unconfirmed_wire(u) for u in answer.unconfirmed[:unconfirmed_n]]
    return body


def to_wire_full(answer: TerminalAnswer) -> dict[str, Any]:
    """The full, unbudgeted encoding `detail` resolves to: every `listed` and `unconfirmed`."""
    body = _decisive(answer, answer.detail)
    body["listed"] = [_node_wire(n) for n in answer.listed]
    body["unconfirmed"] = [_unconfirmed_wire(u) for u in answer.unconfirmed]
    return body


def account_of(answer: TerminalAnswer, path: Path) -> NodeAnswer | None:
    """The answer's account of a vertex: what a child view of `path` must equal (B4-C8)."""
    if answer.primary.path == path:
        return answer.primary
    return next((n for n in answer.listed if n.path == path), None)


# ---- reading a run's durable inputs (the only I/O in this module; `project` above is pure)


@dataclass(frozen=True, slots=True)
class _Group:
    confirmed_gone: bool


def answer_for_run(
    run_dir: FsPath,
    records: Iterable[Mapping[str, Any]],
    terminal_kind: str,
    spec: Mapping[str, Any],
) -> TerminalAnswer:
    """The terminal answer of a finished run, recomputed from the durable inputs U2 wrote: the
    folded lane and its stop rows, the sweep's rows, the group stop, `spec.plan`, and the ledger's
    error record (B4-C1 Post; U2 calls this before the terminal row, the run view at read time).
    A plain plugin's implicit plan serves only admission and the sweep, so its answer is
    plan-less (B2-C10); a plan of an unknown format answers as plan-less too, with its cleanup
    unknown (recovery wrote `sweep_skipped`)."""
    rows = list(records)
    try:
        admitted = fold.plan_of_spec(spec)
    except ValueError:  # UnknownPlanFormat / PlanInvalid: recovery's business, answered plan-less
        admitted = None
    folded = fold.fold_lane(run_dir, admitted)
    started = any(r.get("kind") == "started" for r in rows)
    stop_row = next((r for r in reversed(rows) if r.get("kind") == "group_stop"), None)
    # a run finalized while queued spawned nothing: an empty cleanup and a confirmed-gone group
    # (B2-C12, CB-6); a started run's group is confirmed only by its own `group_stop` row
    group = _Group(True if not started else bool(stop_row and stop_row.get("confirmed_gone")))
    cleanup = (
        sweep.disposition_from_ledger(rows, folded, group) if started else CleanupDisposition()
    )
    error_row = next((r for r in reversed(rows) if r.get("kind") == "error_record"), None)
    answer_plan = admitted if admitted is not None and admitted.declaration_digest else None
    budget = spec.get("summary_budget")
    return project(
        folded,
        cleanup,
        group,
        answer_plan,
        terminal_kind == "interrupted",
        execution_error(terminal_kind, error_row),
        terminal_kind=terminal_kind,
        summary_budget=budget
        if isinstance(budget, int) and not isinstance(budget, bool)
        else bounds.SUMMARY_BUDGET_DEFAULT,
        leased=admitted is not None and bool(admitted.lease_set),
    )


def summary_budget_of(spec: Mapping[str, Any]) -> int:
    budget = spec.get("summary_budget")
    return (
        budget
        if isinstance(budget, int) and not isinstance(budget, bool)
        else bounds.SUMMARY_BUDGET_DEFAULT
    )


def write_finalized(run_dir: FsPath, answer: TerminalAnswer) -> None:
    """Persist the full, unbudgeted encoding `detail` resolves to (`<run_id>/answer`), the answer
    U2 handed over at finalization (`differ d5` compares it with the recomputation). Not a second
    authority: the run view recomputes from the durable inputs."""
    atomic_write_json(run_dir / "evidence" / ANSWER_FILE, to_wire_full(answer))


__all__ = [
    "ANSWER_FILE",
    "GROUP_TARGET",
    "CleanupAnswer",
    "ExecutionErrorAnswer",
    "NodeAnswer",
    "TerminalAnswer",
    "UnconfirmedEffect",
    "account_of",
    "answer_for_run",
    "detail_handle",
    "execution_error",
    "project",
    "to_wire",
    "summary_budget_of",
    "to_wire_full",
    "write_finalized",
]
