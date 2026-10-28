"""Builders for the host-answer tests (L.SV-4.2): a compiled plan, `NodeEnd`s, tickets and the
`FoldedRecord` that U2 would hand to B4, so each B4-C2 rule is exercised on its own case."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from tests.proof.foundations import trees
from trestle.common import lane_format as lf
from trestle.common.plan.compiler import AdmittedPlan, compile
from trestle.server import answer, fold, sweep
from trestle.server.sweep import CleanupDisposition

ROOT_RUN = "run-root-0001"
T0 = datetime(2026, 9, 30, 12, 0, 0, tzinfo=UTC)

# a root with two leaf children `n1`, `n2` (canonical paths "n1", "n2"; ordinals follow)
TWO_LEAVES = ("all", (trees.LEAF, trees.LEAF))


def make_plan(shape: Any = TWO_LEAVES, *, needs_edge: bool = False) -> AdmittedPlan:
    plan = compile(trees.build(shape, needs_edge=needs_edge), {})
    assert isinstance(plan, AdmittedPlan), plan
    return plan


def make_many(n: int) -> AdmittedPlan:
    """A root with `n` leaf children `n1..nn`."""
    plan = compile(trees.build(("all", tuple(trees.LEAF for _ in range(n)))), {})
    assert isinstance(plan, AdmittedPlan), plan
    return plan


def path(text: str) -> tuple[str, ...]:
    return tuple(text.split("/")) if text else ()


def end(
    where: str,
    condition: lf.Condition | None,
    code: str | None = None,
    *,
    cut: lf.Cut | None = None,
    provenance: lf.Provenance | None = None,
    human_action: str | None = None,
    resend: lf.Resend | None = None,
) -> lf.NodeEnd:
    return lf.NodeEnd(
        lineage=lf.Lineage(ROOT_RUN, path(where)),
        at=T0,
        condition=condition,
        code=code,
        human_action=human_action,
        resend=resend,
        provenance=provenance,
        cut=cut,
    )


def satisfied(where: str, *, provenance: lf.Provenance = lf.Provenance.CREATED) -> lf.NodeEnd:
    return end(where, lf.Condition.SATISFIED, provenance=provenance)


def ticket(
    where: str,
    effect: str = "up",
    *,
    repeat: lf.Repeat = lf.Repeat.SAFE,
    confirmation: lf.Confirmation | None = None,
    remedy: lf.RemedyGrant | None = None,
    release: lf.ReleaseDescriptor | None = None,
    released: bool = False,
    result: lf.RecordedResult | None = None,
    attempt: int = 1,
) -> lf.TicketEntry:
    return lf.TicketEntry(
        lineage=lf.Lineage(ROOT_RUN, path(where)),
        effect=effect,
        facet=lf.EffectFacetClass.CREATE,
        attempt=attempt,
        repeat=repeat,
        lifetime=lf.Lifetime.RUN,
        release=release if release is not None else lf.InRunGroup(),
        remedy=remedy,
        issued_at=T0,
        confirmation=confirmation,
        result=result,
        released_at=T0 if released else None,
    )


APPLIED = lf.Confirmation(lf.ConfirmationStatus.APPLIED, None, "sel")


def folded(
    *ends: lf.NodeEnd,
    entries: tuple[lf.TicketEntry, ...] = (),
    stops: tuple[str, ...] = (),
    ended: bool | None = None,
    plan_entry: bool = True,
    unknown_paths: tuple[str, ...] = (),
    overflowed: bool = False,
    refused_full: bool = False,
    plan: AdmittedPlan | None = None,
) -> fold.FoldedRecord:
    """The fold of a lane holding `ends`. `ended` defaults to: every vertex of `plan` has one."""
    if ended is None:
        ended = plan is not None and {e.lineage.path for e in ends} >= plan.selected_scope
    return fold.FoldedRecord(
        root=ROOT_RUN,
        stop_rows=tuple(fold.StopRow(cause, 0, "t") for cause in stops),
        plan=lf.PlanIdentity("d" * 8, "a" * 8, {}, "o" * 8) if plan_entry else None,
        entries=entries,
        steps=(),
        ends=tuple(ends),
        ended=ended,
        unconfirmed=tuple(e for e in entries if e.confirmation is None),
        unknown_paths=unknown_paths,
        overflowed=overflowed,
        refused_full=refused_full,
    )


class GoneGroup:
    confirmed_gone = True


class LiveGroup:
    confirmed_gone = False


def project(
    record: fold.FoldedRecord,
    plan: AdmittedPlan | None,
    *,
    recovered: bool = False,
    error: answer.ExecutionErrorAnswer | None = None,
    cleanup: CleanupDisposition | None = None,
    group: Any = None,
    budget: int = 4096,
    terminal_kind: str | None = None,
) -> answer.TerminalAnswer:
    return answer.project(
        record,
        cleanup if cleanup is not None else CleanupDisposition(released=(sweep.GROUP_TARGET,)),
        group if group is not None else GoneGroup(),
        plan,
        recovered,
        error,
        summary_budget=budget,
        terminal_kind=terminal_kind,
    )
