"""Builders shared by the lane record tests (L.SV-1.x): every written entry class, every optional
field present and absent, and the maximal-field forms V-13 sizes the bounds on."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from trestle.common import lane_format as lf
from trestle.common.plan import bounds

ROOT = "run-root-0001"
WHEN = datetime(2026, 9, 30, 12, 0, 0, 123456, tzinfo=UTC)
LATER = datetime(2026, 9, 30, 12, 5, 30, 654321, tzinfo=UTC)


def lineage(*segments: str, root: str = ROOT) -> lf.Lineage:
    return lf.Lineage(root, tuple(segments))


def max_path() -> tuple[str, ...]:
    """A path of exactly PATH_MAX encoded bytes (three NAME_MAX segments and one of 125)."""
    segments = ("a" * bounds.NAME_MAX,) * 3 + ("a" * 125,)
    assert bounds.text_bytes("/".join(segments)) == bounds.PATH_MAX
    return segments


def lane_file(tmp_path: Path, run_id: str = ROOT) -> Path:
    """`<tmp>/<run_id>/evidence/lane.ndjson`: the run directory's name is the root (B2-I3)."""
    return lf.lane_path(tmp_path / run_id)


ARGV = lf.ArgvRelease(
    executable="/usr/bin/docker",
    observe_argv=("docker", "ps", "-q", "--filter", "name=x"),
    observe_ok_exit=frozenset({0}),
    stop_argv=("docker", "stop", "x"),
    timeout=timedelta(seconds=30),
    remove_argv=("docker", "rm", "x"),
)
ARGV_NO_REMOVE = lf.ArgvRelease(
    executable="/usr/bin/docker",
    observe_argv=("docker", "ps"),
    observe_ok_exit=frozenset({0, 1}),
    stop_argv=("docker", "stop", "x"),
    timeout=timedelta(seconds=2.5),
)
DESCRIPTORS: tuple[lf.ReleaseDescriptor, ...] = (
    lf.InRunGroup(),
    lf.InRunGroup(helpers_disclosed=True),
    ARGV,
    ARGV_NO_REMOVE,
    lf.Durable(lf.DurableOwner.HOST),
    lf.Durable(lf.DurableOwner.ENVIRONMENT),
)
REMEDY = lf.RemedyGrant(code="unit.remedy", effect="up", attempt=2)


def plan_entry(k: int = 1, *, seq: int = 0) -> lf.PlanEntry:
    selection = {("choice", str(i)): ("choice", str(i), "alt") for i in range(k)}
    return lf.PlanEntry(
        lf.PlanIdentity(
            declaration_digest="d" * 64,
            args_hash="a" * 64,
            selection=selection,
            observations_digest="o" * 64,
        ),
        seq=seq,
    )


def issue_entry(
    *path: str,
    effect: str = "up",
    attempt: int = 1,
    release: lf.ReleaseDescriptor | None = None,
    remedy: lf.RemedyGrant | None = None,
    facet: lf.EffectFacetClass = lf.EffectFacetClass.CREATE,
    repeat: lf.Repeat = lf.Repeat.SAFE,
    seq: int = 0,
) -> lf.IssueEntry:
    return lf.IssueEntry(
        lineage=lineage(*path),
        effect=effect,
        facet=facet,
        attempt=attempt,
        repeat=repeat,
        lifetime=lf.Lifetime.RUN,
        release=release if release is not None else lf.InRunGroup(),
        remedy=remedy,
        issued_at=WHEN,
        seq=seq,
    )


def confirmation_entry(
    *path: str,
    effect: str = "up",
    attempt: int = 1,
    status: lf.ConfirmationStatus = lf.ConfirmationStatus.APPLIED,
    code: str | None = None,
    identity: str | None = "sel-1",
    seq: int = 0,
) -> lf.ConfirmationEntry:
    return lf.ConfirmationEntry(
        lineage=lineage(*path),
        effect=effect,
        attempt=attempt,
        confirmation=lf.Confirmation(status, code, identity),
        seq=seq,
    )


def result_entry(
    *path: str,
    effect: str = "test",
    attempt: int = 1,
    passed: bool = True,
    code: str | None = None,
    counts: lf.TestCounts | None = None,
    seq: int = 0,
) -> lf.ResultEntry:
    return lf.ResultEntry(
        lineage=lineage(*path),
        effect=effect,
        attempt=attempt,
        result=lf.RecordedResult(passed, code, counts),
        seq=seq,
    )


def released_entry(
    *path: str,
    effect: str = "up",
    attempt: int = 1,
    outcome: str | None = None,
    seq: int = 0,
) -> lf.ReleasedEntry:
    return lf.ReleasedEntry(
        lineage=lineage(*path),
        effect=effect,
        attempt=attempt,
        released_at=LATER,
        outcome=outcome,
        seq=seq,
    )


def handle(*path: str, effect: str = "up", selector: str = "sel-1") -> lf.CreatedHandle:
    return lf.CreatedHandle(lineage(*path), effect, selector, ARGV)


def step_entry(
    *path: str,
    kind: lf.StepKind = lf.StepKind.FAILED,
    code: str = "unit.raised",
    human_action: str | None = None,
    resend: lf.Resend | None = None,
    with_handle: lf.CreatedHandle | None = None,
    seq: int = 0,
) -> lf.StepEntry:
    return lf.StepEntry(
        lineage=lineage(*path),
        at=WHEN,
        kind=kind,
        code=code,
        human_action=human_action,
        resend=resend,
        handle=with_handle,
        seq=seq,
    )


def node_end(
    *path: str,
    condition: lf.Condition | None = lf.Condition.SATISFIED,
    code: str | None = None,
    human_action: str | None = None,
    resend: lf.Resend | None = None,
    provenance: lf.Provenance | None = lf.Provenance.CREATED,
    cut: lf.Cut | None = None,
    seq: int = 0,
) -> lf.NodeEnd:
    return lf.NodeEnd(
        lineage=lineage(*path),
        at=LATER,
        condition=condition,
        code=code,
        human_action=human_action,
        resend=resend,
        provenance=provenance,
        cut=cut,
        seq=seq,
    )


def every_entry_class() -> list[lf.Entry]:
    """Every written class, each optional field present and absent."""
    entries: list[lf.Entry] = [plan_entry(0), plan_entry(3)]
    for descriptor in DESCRIPTORS:
        entries.append(issue_entry("svc", "db", release=descriptor))
    entries += [
        issue_entry(
            "svc", remedy=REMEDY, facet=lf.EffectFacetClass.SAFE_START, repeat=lf.Repeat.ONCE
        ),
        confirmation_entry("svc"),
        confirmation_entry(
            "svc",
            status=lf.ConfirmationStatus.NOT_APPLIED,
            code="effect.refused",
            identity=None,
        ),
        result_entry("t"),
        result_entry("t", passed=False, code="test.failed", counts=lf.TestCounts(3, 1, 0, 2)),
        released_entry("svc"),
        released_entry("svc", outcome="release.done"),
        step_entry("svc"),
        step_entry(
            "svc",
            kind=lf.StepKind.BLOCKED,
            human_action="Ask the operator to unblock port 8080.",
            resend=lf.Resend.SUCCEEDS_AFTER_ACTION,
            with_handle=handle("svc"),
        ),
        step_entry("svc", kind=lf.StepKind.NO_ACTION, code="unit.no_action"),
        node_end(),
        node_end(
            "svc",
            condition=lf.Condition.BLOCKED,
            code="unit.blocked",
            human_action="Do it.",
            resend=lf.Resend.WILL_NOT_SUCCEED,
            provenance=None,
            cut=lf.Cut.STOPPED,
        ),
        node_end("svc", condition=None, provenance=None, cut=lf.Cut.NOT_STARTED),
    ]
    return entries
