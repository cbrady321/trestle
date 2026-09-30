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


# ---------------------------------------------------------------------------------------------
# SA-01, the loop side (L.SV-5.2, A1c2-11, A1c3-6): `trestle.workflow` cannot import `lane_format`
# (C.5 step 4), so it holds its own record view, and the child adapts field by field. Nothing but
# this node ties the two sides: a field either side renames, or whose wire-level JSON type it
# changes, fails here.

import dataclasses  # noqa: E402
import enum  # noqa: E402
from collections.abc import Mapping, Sequence  # noqa: E402
from datetime import UTC, datetime  # noqa: E402
from typing import Any  # noqa: E402

from trestle.child import run_services as child_services  # noqa: E402
from trestle.workflow import units, values  # noqa: E402


def wire(value: object) -> Any:
    """The JSON form of a value as its own side encodes it on the wire: a path as its canonical
    string, a release descriptor as its wire mapping, an enum as its value, an instant as an
    ISO-8601 UTC string, a set as a sorted array, any other dataclass as an object. Never a
    Python type: the two sides cannot share them."""
    if value is None or isinstance(value, (bool, int, float, str, enum.Enum)):
        return value.value if isinstance(value, enum.Enum) else value
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, (lf.InRunGroup, lf.ArgvRelease, lf.Durable)):
        return lf.descriptor_record(value)
    if isinstance(value, (lf.Lineage, values.Lineage)):
        path = value.path
        segments = path.segments if isinstance(path, values.NodePath) else path
        return {"root_run_id": value.root_run_id, "path": lf.encode_path(tuple(segments))}
    if isinstance(value, Mapping):
        return {str(k): wire(v) for k, v in value.items()}
    if isinstance(value, frozenset):
        return sorted(wire(v) for v in value)
    if isinstance(value, (tuple, list)):
        return [wire(v) for v in value]
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        # `seq` is the file's append order (a property of the file, not of V-4's entry)
        return {
            f.name: wire(getattr(value, f.name))
            for f in dataclasses.fields(value)
            if f.name != "seq"
        }
    raise TypeError(f"no wire form for {type(value).__name__}")


def json_kind(wired: object) -> str:
    if wired is None:
        return "null"
    if isinstance(wired, bool):
        return "boolean"
    if isinstance(wired, (int, float)):
        return "number"
    if isinstance(wired, str):
        return "string"
    return "object" if isinstance(wired, dict) else "array"


def view_conformance(view: object, lane: object) -> list[str]:
    """Every field the view declares exists on the lane's class, with the same wire-level JSON
    type and (for the record pair) the same wire value. Empty when the two agree."""
    theirs = {f.name: getattr(lane, f.name) for f in dataclasses.fields(lane)}  # type: ignore[arg-type]
    problems: list[str] = []
    for f in dataclasses.fields(view):  # type: ignore[arg-type]
        where = f"{type(view).__name__}.{f.name}"
        if f.name not in theirs:
            problems.append(f"{where}: no such field on {type(lane).__name__}")
            continue
        ours, other = wire(getattr(view, f.name)), wire(theirs[f.name])
        if json_kind(ours) != json_kind(other):
            problems.append(f"{where}: wire type {json_kind(ours)} vs {json_kind(other)}")
        elif ours != other:
            problems.append(f"{where}: {ours!r} vs {other!r}")
    return problems


def _record_lane(tmp_path: Path) -> tuple[Path, Any]:
    """A lane whose tickets and steps hold every field of the record present and absent, over
    every descriptor form, written through the AttemptLane."""
    scope = ((), SVC, ("db",))
    lane = make_lane(tmp_path, entries=200, scope=scope, run_id="r_sa01_view")
    run_dir = tmp_path / "r_sa01_view"
    made = ticket(
        issue(lane, *SVC, effect="up", release=sup.ARGV, remedy=sup.REMEDY, max_attempts=3)
    )
    handle = lane.confirm(made, applied("sel-1"))
    assert handle is not None
    lane.record_released(handle, "release.done")
    test = ticket(issue(lane, *SVC, effect="run", facet=lf.EffectFacetClass.EVENT))
    lane.confirm(test, applied(None))
    lane.record_result(test, lf.RecordedResult(False, "test.failed", lf.TestCounts(3, 1, 0, 2)))
    once = ticket(
        issue(
            lane,
            *SVC,
            effect="start",
            facet=lf.EffectFacetClass.SAFE_START,
            repeat=lf.Repeat.ONCE,
            release=lf.Durable(lf.DurableOwner.HOST),
        )
    )
    lane.confirm(once, lf.Confirmation(lf.ConfirmationStatus.NOT_APPLIED, "stop.seen", None))
    grouped = ticket(issue(lane, "db", effect="up", release=lf.InRunGroup(helpers_disclosed=True)))
    lane.confirm(grouped, applied("sel-db"))
    ticket(issue(lane, "db", effect="own", facet=lf.EffectFacetClass.OWNED))  # unconfirmed
    lane.record_step(sup.step_entry(*SVC))
    lane.record_step(
        sup.step_entry(
            *SVC,
            kind=lf.StepKind.BLOCKED,
            code="unit.blocked",
            human_action="Free port 8080.",
            resend=lf.Resend.SUCCEEDS_AFTER_ACTION,
            with_handle=handle,
        )
    )
    lane.record_step(sup.step_entry("db", kind=lf.StepKind.NO_ACTION, code="unit.no_action"))
    return run_dir, lane


@pytest.mark.parametrize("sa", ["SA-01"])
def test_lane_entries_satisfy_workflow_record_view(sa: str, tmp_path: Path) -> None:
    run_dir, lane = _record_lane(tmp_path)
    lane_fields = {
        lf.NodeRecord: {f.name for f in dataclasses.fields(lf.NodeRecord)},
        lf.TicketEntry: {f.name for f in dataclasses.fields(lf.TicketEntry)},
        lf.StepEntry: {f.name for f in dataclasses.fields(lf.StepEntry)},
    }
    views = {
        units.NodeRecordView: lf.NodeRecord,
        units.TicketView: lf.TicketEntry,
        units.StepView: lf.StepEntry,
    }
    for view_cls, lane_cls in views.items():  # the declared shapes, before any value
        declared = {f.name for f in dataclasses.fields(view_cls)}
        assert declared <= lane_fields[lane_cls], (
            view_cls.__name__,
            declared - lane_fields[lane_cls],
        )
    assert {f.name for f in dataclasses.fields(units.NodeRecordView)} == lane_fields[lf.NodeRecord]
    ticket_fields = [f.name for f in dataclasses.fields(units.TicketView)]
    assert ticket_fields == [f.name for f in dataclasses.fields(lf.TicketEntry)]  # V-4's order
    # a StepEntry's `seq` is the file's append order, not a field of V-4's entry
    step_fields = [f.name for f in dataclasses.fields(units.StepView)]
    assert step_fields == [f.name for f in dataclasses.fields(lf.StepEntry) if f.name != "seq"]

    seen: dict[str, set[str]] = {}  # field -> the wire kinds observed on the lane side
    for path in ((), SVC, ("db",)):
        record = lane.node_record(path)
        view = child_services.node_record_view(record)
        assert wire(view) == wire(record), path  # the whole record, on the wire form
        assert len(view.tickets) == len(record.tickets) and len(view.steps) == len(record.steps)
        for v, r in [
            *zip(view.tickets, record.tickets, strict=True),
            *zip(view.steps, record.steps, strict=True),
        ]:
            assert view_conformance(v, r) == []
            for f in dataclasses.fields(v):
                seen.setdefault(f"{type(v).__name__}.{f.name}", set()).add(
                    json_kind(wire(getattr(r, f.name)))
                )
    # vacuity: every field was exercised, every optional field present and absent
    every = {f"TicketView.{n}" for n in ticket_fields} | {f"StepView.{n}" for n in step_fields}
    assert set(seen) == every
    for name in every:
        assert seen[name] - {"null"}, f"{name} never held a value"
    for name in (
        "TicketView.remedy",
        "TicketView.confirmation",
        "TicketView.handle",
        "TicketView.result",
        "TicketView.released_at",
        "TicketView.release_outcome",
        "StepView.human_action",
        "StepView.resend",
        "StepView.handle",
    ):
        assert "null" in seen[name], f"{name} never absent"
    assert lane_fields[lf.NodeRecord] == {"tickets", "steps"} and run_dir.exists()


def _first_pair(
    tmp_path: Path,
) -> tuple[units.TicketView, lf.TicketEntry, units.StepView, lf.StepEntry]:
    _, lane = _record_lane(tmp_path)
    record = lane.node_record(SVC)
    view = child_services.node_record_view(record)
    return view.tickets[0], record.tickets[0], view.steps[1], record.steps[1]


def _renamed(obj: object, old: str, new: str) -> object:
    fields = [(new if f.name == old else f.name, object) for f in dataclasses.fields(obj)]  # type: ignore[arg-type]
    cls = dataclasses.make_dataclass(type(obj).__name__, fields, frozen=True)
    return cls(
        **{
            (new if f.name == old else f.name): getattr(obj, f.name)
            for f in dataclasses.fields(obj)
        }
    )  # type: ignore[arg-type]


def test_planted_record_view_defects_fail_on_either_side(tmp_path: Path) -> None:
    tview, tlane, sview, slane = _first_pair(tmp_path)
    assert view_conformance(tview, tlane) == [] and view_conformance(sview, slane) == []
    # a renamed field, on either side
    assert view_conformance(_renamed(tview, "effect", "effect_id"), tlane)
    assert view_conformance(tview, _renamed(tlane, "effect", "effect_id"))
    assert view_conformance(_renamed(sview, "human_action", "action"), slane)
    assert view_conformance(sview, _renamed(slane, "code", "stable_code"))
    # a wire-level type change, on either side
    assert view_conformance(dataclasses.replace(tview, attempt="1"), tlane)  # type: ignore[arg-type]
    assert view_conformance(tview, dataclasses.replace(tlane, attempt="1"))  # type: ignore[arg-type]
    assert view_conformance(dataclasses.replace(tview, release=None), tlane)
    assert view_conformance(tview, dataclasses.replace(tlane, issued_at="2026-09-30"))  # type: ignore[arg-type]
    assert view_conformance(dataclasses.replace(sview, resend=None), slane)
    assert view_conformance(sview, dataclasses.replace(slane, at=1))  # type: ignore[arg-type]
    # a changed value on the same type is not conformance either
    assert view_conformance(dataclasses.replace(tview, effect="other"), tlane)


def test_every_lane_entry_class_written_round_trips_through_the_adapter(tmp_path: Path) -> None:
    run_dir, wrote = _write_every_class(tmp_path)
    folded_paths: Sequence[tuple[str, ...]] = ((), SVC)
    lane_read = lf.read_lane(lf.lane_path(run_dir))
    for path in folded_paths:
        record = lf.node_record(lane_read.entries, path)
        view = child_services.node_record_view(record)
        assert wire(view) == wire(record)
    assert {cls for cls, _ in wrote} == set(records.LANE_SCHEMA)
