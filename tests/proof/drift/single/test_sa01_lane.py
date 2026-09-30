"""SA-01 in single (L.SV-1.4): what the child writes through `AttemptLane`, what the proof
court's strict oracle reads and what the host folds are one record, entry by entry."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.proof import record_facts, records
from tests.single.record import support as sup
from tests.single.record.test_attempt_lane import applied, issue, make_lane, ticket
from tests.single.record.test_fold import Accepted
from trestle.common import lane_format as lf
from trestle.server import fold

SVC = ("svc",)


def _write_every_class(tmp_path: Path) -> tuple[Path, list[tuple[str, str | None]]]:
    """A lane holding every written entry class, a NodeEnd included, written through the
    AttemptLane; returns its run directory and the (class, path) sequence written."""
    scope = ((), SVC)
    lane = make_lane(tmp_path, entries=64, scope=scope, run_id="r_sa01_roundtrip")
    run_dir = tmp_path / "r_sa01_roundtrip"
    wrote: list[tuple[str, str | None]] = [("plan", None)]

    made = ticket(issue(lane, *SVC, effect="up", release=sup.ARGV))
    wrote.append(("issue", "svc"))
    handle = lane.confirm(made, applied("sel-1"))
    wrote.append(("confirmation", "svc"))
    assert handle is not None
    test = ticket(issue(lane, *SVC, effect="run", facet=lf.EffectFacetClass.EVENT))
    wrote.append(("issue", "svc"))
    lane.confirm(test, applied(None))
    wrote.append(("confirmation", "svc"))
    lane.record_result(test, lf.RecordedResult(True, None, lf.TestCounts(4, 0, 0, 1)))
    wrote.append(("result", "svc"))
    lane.record_step(
        sup.step_entry(
            *SVC,
            kind=lf.StepKind.BLOCKED,
            code="unit_blocked",
            human_action="Free port 8080.",
            resend=lf.Resend.SUCCEEDS_AFTER_ACTION,
        )
    )
    wrote.append(("step", "svc"))
    lane.record_released(handle, "release.done")
    wrote.append(("released", "svc"))
    lane.record_step(sup.step_entry(*SVC, code="release_note", with_handle=handle))
    wrote.append(("step", "svc"))
    lane.record_end(sup.node_end(*SVC, condition=lf.Condition.BLOCKED, code="unit_blocked"))
    wrote.append(("end", "svc"))
    lane.record_end(sup.node_end())
    wrote.append(("end", ""))
    return run_dir, wrote


@pytest.mark.parametrize("sa", ["SA-01"])
def test_child_write_seam_read_fold_roundtrip(sa: str, tmp_path: Path) -> None:
    run_dir, wrote = _write_every_class(tmp_path)
    assert {cls for cls, _ in wrote} == set(records.LANE_SCHEMA)  # every written class

    # the seam's oracle, never the product codec
    lane = records.lane_rows(run_dir)
    assert not lane.torn and not lane.problems and lane.unknown == 0
    assert lane.format == records.LANE_FORMAT == 1
    assert [(r.cls, "" if r.path is None and r.cls != "plan" else r.path) for r in lane.rows] == [
        (cls, path) for cls, path in wrote
    ]
    assert [r.entry["seq"] for r in lane.rows] == list(range(1, len(wrote) + 1))

    # the fold
    folded = fold.fold_lane(run_dir, Accepted(frozenset({(), SVC}), 64))
    verdict = record_facts.fold_equals_lane(lane, folded, scope=((), SVC))
    assert verdict.ok, verdict.violations
    assert folded.ended and not folded.overflowed and folded.unknown_paths == ()

    # entry by entry, through the seam's node_record: the NodeEnd is an end, never a step
    svc = records.node_record(run_dir, SVC)
    assert [s["code"] for s in svc.steps] == ["unit_blocked", "release_note"]
    assert [e["code"] for e in svc.ends] == ["unit_blocked"]
    assert [t["confirmation"].entry["status"] for t in svc.tickets] == ["applied", "applied"]
    assert svc.tickets[0]["released"] is not None and svc.tickets[1]["result"] is not None
    assert [s.code for s in folded.steps] == ["unit_blocked", "release_note"]
    assert [e.code for e in folded.ends if e.lineage.path == SVC] == ["unit_blocked"]
    root = records.node_record(run_dir)
    assert root.kinds == [] and root.terminal is None and len(root.ends) == 1
    # a claim precedes every effect record
    assert record_facts.claim_before_effect(lane).ok
