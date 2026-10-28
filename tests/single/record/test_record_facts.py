"""L.SV-1.4: the record-fact checkers catch their planted defects and refuse to pass on an empty
lane (vacuity guards); only the codec, the writer and the fold import the product codec."""

from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path

from tests.proof import record_facts, records
from tests.single.record import support as sup
from tests.single.record.test_attempt_lane import applied, issue, make_lane, ticket
from tests.single.record.test_fold import Accepted
from trestle.common import lane_format as lf
from trestle.common.fsutil import append_ndjson
from trestle.server import fold

REPO = Path(__file__).resolve().parents[3]


def _append(run_dir: Path, seq: int, entry: lf.Entry) -> None:
    """A planted line, past the AttemptLane's own order and refusals."""
    append_ndjson(lf.lane_path(run_dir), lf.encode_record(entry, seq))


def _good_lane(tmp_path: Path, name: str = "r_good") -> Path:
    lane = make_lane(tmp_path, entries=64, scope=((),), run_id=name)
    made = ticket(issue(lane, effect="up", release=sup.ARGV))
    lane.confirm(made, applied("sel"))
    lane.record_step(sup.step_entry(code="unit_note", kind=lf.StepKind.NO_ACTION))
    lane.record_end(sup.node_end())
    return tmp_path / name


def test_claim_before_effect_catches_planted_effect_first(tmp_path: Path) -> None:
    assert record_facts.claim_before_effect(records.lane_rows(_good_lane(tmp_path))).ok
    # an effect record with no claim at all
    bare = tmp_path / "r_no_claim"
    _append(bare, 1, sup.plan_entry(0))
    _append(bare, 2, sup.confirmation_entry(effect="up"))
    verdict = record_facts.claim_before_effect(records.lane_rows(bare))
    assert not verdict.ok and not verdict.vacuous
    assert "no earlier issue entry" in verdict.violations[0]
    # the claim written after the effect record is still a violation (the order is the fact)
    late = tmp_path / "r_claim_late"
    _append(late, 1, sup.plan_entry(0))
    _append(late, 2, sup.confirmation_entry(effect="up"))
    _append(late, 3, sup.issue_entry(effect="up"))
    assert not record_facts.claim_before_effect(records.lane_rows(late)).ok
    # a result or a release with no claim is as wrong as a confirmation
    for entry in (sup.result_entry(effect="up"), sup.released_entry(effect="up")):
        other = tmp_path / f"r_orphan_{lf.entry_class(entry)}"
        _append(other, 1, sup.plan_entry(0))
        _append(other, 2, entry)
        assert not record_facts.claim_before_effect(records.lane_rows(other)).ok
    # another attempt's claim does not cover this attempt's effect
    wrong = tmp_path / "r_wrong_attempt"
    _append(wrong, 1, sup.plan_entry(0))
    _append(wrong, 2, sup.issue_entry(effect="up", attempt=1))
    _append(wrong, 3, sup.confirmation_entry(effect="up", attempt=2))
    assert not record_facts.claim_before_effect(records.lane_rows(wrong)).ok


def test_fold_equals_lane_catches_planted_drop(tmp_path: Path) -> None:
    run_dir = _good_lane(tmp_path)
    lane = records.lane_rows(run_dir)
    accepted = Accepted(frozenset({()}), 64)
    folded = fold.fold_lane(run_dir, accepted)
    assert record_facts.fold_equals_lane(lane, folded).ok
    assert folded.steps and folded.entries
    for name, drifted in (
        ("steps", replace(folded, steps=folded.steps[:-1])),
        ("entries", replace(folded, entries=())),
        ("plan", replace(folded, plan=None)),
        ("ended", replace(folded, ended=not folded.ended)),
        # a ticket whose confirmation the fold lost is a drift of its entries
        ("entries", replace(folded, entries=(replace(folded.entries[0], confirmation=None),))),
    ):
        verdict = record_facts.fold_equals_lane(lane, drifted)
        assert not verdict.ok, name
        assert any(name in v for v in verdict.violations), (name, verdict.violations)


def test_fold_equals_lane_catches_planted_dropped_node_end(tmp_path: Path) -> None:
    run_dir = _good_lane(tmp_path)
    lane = records.lane_rows(run_dir)
    folded = fold.fold_lane(run_dir, Accepted(frozenset({()}), 64))
    assert len(folded.ends) == 1 and folded.ended
    dropped = replace(folded, ends=())
    verdict = record_facts.fold_equals_lane(lane, dropped)
    assert not verdict.ok and any(v.startswith("ends:") for v in verdict.violations)
    # dropping the NodeEnd and lying that the run ended anyway is caught twice over
    lying = record_facts.fold_equals_lane(lane, replace(folded, ends=(), ended=True))
    assert not lying.ok and any(v.startswith("ends:") for v in lying.violations)
    # a NodeEnd the fold kept when it was the second, not the first, is caught too
    second = sup.node_end(condition=lf.Condition.FAILED, code="second")
    _append(run_dir, len(lane.rows) + 1, second)
    lane_two = records.lane_rows(run_dir)
    refolded = fold.fold_lane(run_dir, Accepted(frozenset({()}), 64))
    assert record_facts.fold_equals_lane(lane_two, refolded).ok  # the first stands in both
    swapped = replace(refolded, ends=(replace(refolded.ends[0], code="second"),))
    assert not record_facts.fold_equals_lane(lane_two, swapped).ok


def test_vacuity_guard_empty_lane(tmp_path: Path) -> None:
    empty = records.lane_rows(tmp_path / "r_no_lane")
    assert empty.rows == []
    folded = fold.fold_lane(tmp_path / "r_no_lane", None)
    checks = (
        record_facts.claim_before_effect(empty),
        record_facts.fold_equals_lane(empty, folded),
        record_facts.applied_past_offset(empty, 0),
    )
    for verdict in checks:
        assert not verdict.ok and verdict.vacuous  # an empty lane proves nothing
    # applied_past_offset: a length U2 could not read is unproven, never passed (B2-C15)
    run_dir = _good_lane(tmp_path)
    lane = records.lane_rows(run_dir)
    unread = record_facts.applied_past_offset(lane, None)
    assert not unread.ok and unread.unproven and not unread.vacuous
    # ... and the offset fact itself: APPLIED entries at or past the recorded length
    confirmation = next(r for r in lane.rows if r.cls == "confirmation")
    assert record_facts.applied_past_offset(lane, confirmation.end).ok
    caught = record_facts.applied_past_offset(lane, confirmation.offset)
    assert not caught.ok and "past offset" in caught.violations[0]


def test_applied_past_offset_counts_only_applied_non_release_entries(tmp_path: Path) -> None:
    lane = make_lane(tmp_path, entries=64, scope=((),), run_id="r_offset")
    run_dir = tmp_path / "r_offset"
    before = ticket(issue(lane, effect="a"))
    lane.confirm(before, applied("s-a"))
    offset = lane.committed_length()  # the length a stop row would record
    seen = ticket(issue(lane, effect="a"))  # a stop seen after the offset: not an applied effect
    stop_seen = lf.Confirmation(lf.ConfirmationStatus.NOT_APPLIED, "stop_seen", None)
    assert lane.confirm(seen, stop_seen) is None
    later = ticket(issue(lane, effect="b"))
    handle = lane.confirm(later, applied("s-b"))
    assert handle is not None
    lane.record_released(handle, "release.done")  # a release past the offset
    verdict = record_facts.applied_past_offset(records.lane_rows(run_dir), offset)
    # exactly the one APPLIED confirmation of effect b is past the offset; the NOT_APPLIED
    # stop-seen and the released entry are not counted
    assert [v.split()[1] for v in verdict.violations] == ["b"], verdict.violations


def test_only_writer_fold_import_codec() -> None:
    """The product codec has four readers: itself, the writer, the fold and the child's
    run-services adapter (L.SV-5.2: it converts the lane's records into the workflow package's
    record view, which cannot import the codec). The proof seam and the record facts import none
    of it (a codec defect and a product defect must not hide each other)."""

    def imports_codec(path: Path) -> bool:
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ImportFrom):
                names = {a.name for a in node.names}
                if node.module == "trestle.common.lane_format":
                    return True
                if node.module == "trestle.common" and "lane_format" in names:
                    return True
            elif isinstance(node, ast.Import):
                if any(a.name == "trestle.common.lane_format" for a in node.names):
                    return True
        return False

    importers = sorted(
        str(p.relative_to(REPO)) for p in (REPO / "trestle").rglob("*.py") if imports_codec(p)
    )
    assert importers == [
        "trestle/child/attempt_lane.py",
        "trestle/child/run_services.py",
        "trestle/server/fold.py",
    ], importers
    for seam in ("records.py", "record_facts.py"):
        assert not imports_codec(REPO / "tests" / "proof" / seam), seam
