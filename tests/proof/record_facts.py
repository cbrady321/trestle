"""Record-fact checkers over the lane (L.SV-1.4; MC-19): three facts a proof reads from a run's
committed record, each with a vacuity guard (a checker that passes on an empty lane proves
nothing, so it fails there).

They read only the seam (`tests.proof.records.lane_rows`, the independent strict oracle) and a
`FoldedRecord`'s attributes; none imports the product's lane codec.

    claim_before_effect  every effect record is preceded, in lane order, by its own claim: an
                         issue entry carrying its release descriptor (B2-C7, WR-OWN)
    fold_equals_lane     the host's fold holds exactly the tickets, steps, ends and `ended` the
                         lane holds, entry by entry (B2-C7, V-4.8)
    applied_past_offset  APPLIED non-release entries past a stop row's `lane_committed_length`
                         (used by L.SV-5.12); a length of None is reported unproven, never passed
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from tests.proof.records import LaneRows, encode_path, lane_tickets


@dataclass(frozen=True)
class Verdict:
    ok: bool
    violations: tuple[str, ...] = ()
    vacuous: bool = False  # the lane held nothing, so nothing was proven
    unproven: bool = False  # the input the proof needs was absent (a stop row's length is None)


def _vacuous(lane: LaneRows) -> Verdict | None:
    if not lane.rows:
        return Verdict(False, ("the lane holds no entry: nothing is proven",), vacuous=True)
    return None


def claim_before_effect(lane: LaneRows) -> Verdict:
    """Every confirmation, result and released entry has, earlier in lane order, the issue entry
    of its ticket (path, effect, attempt), and that issue entry carries a release descriptor."""
    empty = _vacuous(lane)
    if empty is not None:
        return empty
    violations: list[str] = []
    issued: dict[tuple[str, str, int], int] = {}
    for row in lane.rows:
        e = row.entry
        if row.cls == "issue":
            release = e["release"]
            if not (isinstance(release, dict) and release.get("form")):
                violations.append(f"issue at byte {row.offset} carries no release descriptor")
            issued.setdefault((e["path"], e["effect"], e["attempt"]), row.offset)
        elif row.cls in ("confirmation", "result", "released"):
            key = (e["path"], e["effect"], e["attempt"])
            if key not in issued:
                violations.append(
                    f"{row.cls} at byte {row.offset} for {key} has no earlier issue entry"
                )
    return Verdict(not violations, tuple(violations))


def _walked(scope: Iterable[tuple[str, ...]], selection: dict[str, str]) -> set[tuple[str, ...]]:
    """`V_run` (V-4.8), derived here from the recorded selection: the scope minus every
    alternative not selected and its subtree (an alternative is a child of its CHOICE)."""
    chosen = {tuple(k.split("/")) if k else (): tuple(v.split("/")) for k, v in selection.items()}
    walked = set()
    for path in scope:
        cut = any(
            chosen.get(path[:depth][:-1]) not in (None, path[:depth])
            for depth in range(1, len(path) + 1)
        )
        if not cut:
            walked.add(path)
    return walked


def _ticket_facts_lane(lane: LaneRows, scope: set[tuple[str, ...]]) -> list[tuple[Any, ...]]:
    wanted = {encode_path(p) for p in scope}
    facts = []
    for t in lane_tickets(lane):
        issue = t["issue"]
        if issue.path not in wanted:
            continue
        conf = t["confirmation"]
        facts.append(
            (
                issue.path,
                issue.entry["effect"],
                issue.entry["attempt"],
                conf.entry["status"] if conf else None,
                t["result"] is not None,
                t["released"] is not None,
            )
        )
    return facts


def _ticket_facts_fold(folded: Any) -> list[tuple[Any, ...]]:
    facts = []
    for t in folded.entries:
        conf = t.confirmation
        facts.append(
            (
                encode_path(t.lineage.path),
                t.effect,
                t.attempt,
                str(conf.status.value) if conf is not None else None,
                t.result is not None,
                t.released_at is not None,
            )
        )
    return facts


def fold_equals_lane(
    lane: LaneRows, folded: Any, scope: Sequence[tuple[str, ...]] = ((),)
) -> Verdict:
    """`folded` (a `FoldedRecord`) equals what the lane holds for the vertices of `scope`: the
    plan, every ticket (with its confirmation, result and release), every step, the first
    NodeEnd per vertex (a later one is dropped), and `ended` (a NodeEnd for every vertex of the
    walked set, false with no plan entry)."""
    empty = _vacuous(lane)
    if empty is not None:
        return empty
    in_scope = set(scope)
    where = {encode_path(p) for p in in_scope}
    violations: list[str] = []

    plans = [r.entry for r in lane.rows if r.cls == "plan"]
    if (folded.plan is not None) != bool(plans):
        violations.append("plan: the fold and the lane disagree on whether a plan was recorded")
    elif plans and folded.plan.declaration_digest != plans[0]["declaration_digest"]:
        violations.append("plan: declaration_digest differs")

    if _ticket_facts_fold(folded) != _ticket_facts_lane(lane, in_scope):
        violations.append("entries: the fold's tickets differ from the lane's")

    lane_steps = [
        (r.path, r.entry["kind"], r.entry["code"], r.entry["human_action"], r.entry["resend"])
        for r in lane.rows
        if r.cls == "step" and r.path in where
    ]
    fold_steps = [
        (
            encode_path(s.lineage.path),
            s.kind.value,
            s.code,
            s.human_action,
            s.resend.value if s.resend is not None else None,
        )
        for s in folded.steps
    ]
    if fold_steps != lane_steps:
        violations.append("steps: the fold's steps differ from the lane's")

    first_ends: dict[str, dict[str, Any]] = {}
    for r in lane.rows:
        if r.cls == "end" and r.path in where:
            first_ends.setdefault(r.path or "", r.entry)
    lane_ends = [(p, e["condition"], e["code"]) for p, e in first_ends.items()]
    fold_ends = [
        (
            encode_path(e.lineage.path),
            e.condition.value if e.condition is not None else None,
            e.code,
        )
        for e in folded.ends
    ]
    if fold_ends != lane_ends:
        violations.append(
            "ends: the fold's NodeEnds differ from the lane's first NodeEnd per vertex"
        )

    selection = plans[0]["selection"] if plans else {}
    walked = {encode_path(p) for p in _walked(in_scope, selection)}
    lane_ended = bool(plans) and walked <= set(first_ends)
    if folded.ended != lane_ended:
        violations.append(f"ended: the fold says {folded.ended}, the lane says {lane_ended}")
    return Verdict(not violations, tuple(violations))


def applied_past_offset(lane: LaneRows, committed_length: int | None) -> Verdict:
    """The APPLIED confirmations (a release is a different class, and a `NOT_APPLIED` such as a
    stop seen is not an applied effect) that begin at or past `committed_length`, the lane length
    a stop row recorded. `None` means U2 could not read the length: the proof is reported
    unproven, never passed (B2-C15)."""
    empty = _vacuous(lane)
    if empty is not None:
        return empty
    if committed_length is None:
        return Verdict(False, ("the stop row's lane_committed_length is None",), unproven=True)
    violations = [
        f"APPLIED {row.entry['effect']} at byte {row.offset} is past offset {committed_length}"
        for row in lane.rows
        if row.cls == "confirmation"
        and row.entry["status"] == "applied"
        and row.offset >= committed_length
    ]
    return Verdict(not violations, tuple(violations))
