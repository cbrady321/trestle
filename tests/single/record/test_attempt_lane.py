"""L.SV-1.2: the child AttemptLane (B2-C7): order, durability, one serialized appender,
append-only past the committed length, `lane_entries` capacity with reserved slots, and the
refusals the lane owns (V-4)."""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

import pytest

from tests.single.record import support as sup
from trestle.child.attempt_lane import AttemptLane, AttemptTicket, TicketRefusal
from trestle.common import lane_format as lf
from trestle.common.plan import bounds

ROOT_PATH: tuple[str, ...] = ()
Scope = tuple[tuple[str, ...], ...]


class Port:
    """The machine port a facet would call after `issue` returned a ticket: the spy."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self) -> None:
        self.calls += 1


def make_lane(
    tmp_path: Path,
    *,
    entries: int = bounds.LANE_BASE_ENTRIES,
    scope: Scope = (ROOT_PATH,),
    plan: bool = True,
    run_id: str = sup.ROOT,
    **kwargs: Any,
) -> AttemptLane:
    lane = AttemptLane(tmp_path / run_id, entries, scope, **kwargs)
    if plan:
        lane.record_plan(sup.plan_entry(0).plan)
    return lane


def lane_bytes(tmp_path: Path, run_id: str = sup.ROOT) -> bytes:
    path = lf.lane_path(tmp_path / run_id)
    return path.read_bytes() if path.exists() else b""


def issue(
    lane: AttemptLane,
    *path: str,
    effect: str = "up",
    facet: lf.EffectFacetClass = lf.EffectFacetClass.CREATE,
    repeat: lf.Repeat = lf.Repeat.SAFE,
    max_attempts: int = 5,
    release: lf.ReleaseDescriptor | None = None,
    remedy: lf.RemedyGrant | None = None,
    port: Port | None = None,
) -> AttemptTicket | TicketRefusal:
    """`Ticketed`'s shape: the port is called only when a ticket came back."""
    result = lane.issue(
        sup.lineage(*path),
        effect,
        facet,
        repeat,
        lf.Lifetime.RUN,
        release if release is not None else lf.InRunGroup(),
        max_attempts,
        remedy,
    )
    if isinstance(result, AttemptTicket) and port is not None:
        port()
    return result


def ticket(result: AttemptTicket | TicketRefusal) -> AttemptTicket:
    assert isinstance(result, AttemptTicket), result
    return result


def applied(identity: str | None = "sel-1") -> lf.Confirmation:
    return lf.Confirmation(lf.ConfirmationStatus.APPLIED, None, identity)


def test_issue_returns_only_after_fsync(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lane = make_lane(tmp_path)
    lane_file = lf.lane_path(tmp_path / sup.ROOT)
    events: list[str] = []
    snapshots: list[bytes] = []
    real_fsync = os.fsync

    def spy(fd: int) -> None:
        events.append("fsync")
        snapshots.append(lane_file.read_bytes())
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", spy)
    result = issue(lane, "svc", port=Port())
    events.append("returned")
    assert isinstance(result, AttemptTicket)
    assert events[-1] == "returned" and events.count("fsync") >= 2  # the file, then its directory
    assert b'"class":"issue"' in snapshots[0]  # the entry was in the file when it was fsync'd
    read = lf.read_lane(lane_file)
    assert isinstance(read.entries[-1], lf.IssueEntry)


def test_record_plan_precedes_issue_else_refused(tmp_path: Path) -> None:
    lane = make_lane(tmp_path, plan=False)
    port = Port()
    assert issue(lane, "svc", port=port) is TicketRefusal.PLAN_NOT_RECORDED
    assert port.calls == 0 and lane_bytes(tmp_path) == b""
    lane.record_plan(sup.plan_entry(0).plan)
    assert isinstance(issue(lane, "svc", port=port), AttemptTicket) and port.calls == 1
    with pytest.raises(RuntimeError):  # exactly once
        lane.record_plan(sup.plan_entry(0).plan)


def test_issue_entry_carries_facet_and_remedy(tmp_path: Path) -> None:
    lane = make_lane(tmp_path)
    remedy = lf.RemedyGrant("unit.remedy", "up", 1)
    first = ticket(
        issue(
            lane,
            "svc",
            facet=lf.EffectFacetClass.SAFE_START,
            repeat=lf.Repeat.ONCE,
            remedy=remedy,
            release=sup.ARGV,
        )
    )
    assert first.facet == lf.EffectFacetClass.SAFE_START and first.remedy == remedy
    (entry,) = [e for e in lf.read_lane(lf.lane_path(tmp_path / sup.ROOT)).entries[1:]]
    assert isinstance(entry, lf.IssueEntry)
    assert entry.facet == lf.EffectFacetClass.SAFE_START
    assert entry.remedy == remedy and entry.release == sup.ARGV
    assert entry.repeat == lf.Repeat.ONCE and entry.attempt == 1


def test_confirm_returns_handle_only_for_applied_create(tmp_path: Path) -> None:
    lane = make_lane(tmp_path)
    create = ticket(issue(lane, "a", release=sup.ARGV))
    handle = lane.confirm(create, applied("sel-a"))
    assert handle == lf.CreatedHandle(create.lineage, "up", "sel-a", sup.ARGV)
    owned = ticket(issue(lane, "b", facet=lf.EffectFacetClass.OWNED))
    assert lane.confirm(owned, applied("sel-b")) is None  # APPLIED but not a CREATE
    not_applied = ticket(issue(lane, "c"))
    refused = lf.Confirmation(lf.ConfirmationStatus.NOT_APPLIED, "effect.refused", None)
    assert lane.confirm(not_applied, refused) is None
    unknown = ticket(issue(lane, "d"))
    assert lane.confirm(unknown, lf.Confirmation(lf.ConfirmationStatus.UNKNOWN, None, None)) is None
    with pytest.raises(ValueError):  # an APPLIED creation must carry its selector
        lane.confirm(ticket(issue(lane, "e")), applied(None))
    with pytest.raises(ValueError):  # a ticket is confirmed once
        lane.confirm(create, applied("sel-a"))


def test_record_result_and_step_roundtrip_via_node_record(tmp_path: Path) -> None:
    lane = make_lane(tmp_path)
    event = ticket(issue(lane, "t", effect="test", facet=lf.EffectFacetClass.EVENT))
    lane.confirm(event, applied(None))
    result = lf.RecordedResult(False, "test.failed", lf.TestCounts(3, 1, 0, 2))
    lane.record_result(event, result)
    step = sup.step_entry(
        "t",
        kind=lf.StepKind.BLOCKED,
        code="unit.blocked",
        human_action="Fix it.",
        resend=lf.Resend.UNKNOWN,
    )
    assert lane.record_step(step) is None
    record = lane.node_record(("t",))
    (entry,) = record.tickets
    assert entry.result == result and entry.confirmation == applied(None)
    assert entry.handle is None and entry.facet == lf.EffectFacetClass.EVENT
    (held,) = record.steps
    assert held == step and held.seq > 0
    assert lane.node_record(("other",)) == lf.NodeRecord((), ())
    # the durable file yields the same record
    read = lf.read_lane(lf.lane_path(tmp_path / sup.ROOT))
    assert lf.node_record(read.entries, ("t",)) == record


def test_record_released_after_handle(tmp_path: Path) -> None:
    lane = make_lane(tmp_path)
    created = ticket(issue(lane, "svc", release=sup.ARGV))
    handle = lane.confirm(created, applied("sel-x"))
    assert handle is not None
    assert lane.node_record(("svc",)).tickets[0].released_at is None
    lane.record_released(handle, "release.done")
    (entry,) = lane.node_record(("svc",)).tickets
    assert entry.released_at is not None and entry.release_outcome == "release.done"
    stranger = lf.CreatedHandle(sup.lineage("svc"), "up", "never-confirmed", sup.ARGV)
    with pytest.raises(ValueError):
        lane.record_released(stranger, None)


def test_concurrent_appends_serialized_no_loss(tmp_path: Path) -> None:
    threads, per_thread = 8, 200
    scope = tuple((f"t{i}",) for i in range(threads))
    lane = make_lane(tmp_path, entries=6000, scope=scope)
    errors: list[BaseException] = []

    def work(index: int) -> None:
        try:
            for _ in range(per_thread):
                result = issue(lane, f"t{index}", max_attempts=10_000)
                assert isinstance(result, AttemptTicket), result
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    pool = [threading.Thread(target=work, args=(i,)) for i in range(threads)]
    for t in pool:
        t.start()
    for t in pool:
        t.join()
    assert not errors
    data = lane_bytes(tmp_path)
    lines = data.split(b"\n")[:-1]
    assert len(lines) == 1 + threads * per_thread  # nothing lost, nothing merged
    read = lf.read_lane(lf.lane_path(tmp_path / sup.ROOT))
    assert not read.torn and len(read.entries) == len(lines)
    seqs = [e.seq for e in read.entries]
    assert seqs == list(range(1, len(lines) + 1))  # strictly increasing, no gap
    for index in range(threads):  # per-node attempt numbers are 1..200
        attempts = [
            e.attempt
            for e in read.entries
            if isinstance(e, lf.IssueEntry) and e.lineage.path == (f"t{index}",)
        ]
        assert attempts == list(range(1, per_thread + 1))


def test_committed_length_monotone_repair_drops_only_uncommitted_tail(tmp_path: Path) -> None:
    lane = make_lane(tmp_path)
    lane_file = lf.lane_path(tmp_path / sup.ROOT)
    lengths = [lane.committed_length()]
    ticket(issue(lane, "svc"))
    lengths.append(lane.committed_length())
    committed = lane_file.read_bytes()
    assert lengths[-1] == len(committed) > lengths[0]
    with lane_file.open("ab") as fh:
        fh.write(b'{"class":"issue","seq":99,"pa')  # a planted torn tail
    assert lane.committed_length() == len(committed)  # the torn tail is not committed
    ticket(issue(lane, "svc"))
    lengths.append(lane.committed_length())
    after = lane_file.read_bytes()
    assert after.startswith(committed)  # no committed byte rewritten
    assert b'"seq":99' not in after  # only the uncommitted tail was dropped
    ticket(issue(lane, "svc"))
    lengths.append(lane.committed_length())
    assert lengths == sorted(lengths) and len(set(lengths)) == len(lengths)


def test_attempt_numbered_per_path_effect(tmp_path: Path) -> None:
    lane = make_lane(tmp_path, scope=(("a",), ("b",)))
    assert [issue(lane, "a", effect="up") for _ in range(2)] == [
        AttemptTicket(
            sup.lineage("a"),
            "up",
            lf.EffectFacetClass.CREATE,
            n,
            lf.Repeat.SAFE,
            lf.Lifetime.RUN,
            lf.InRunGroup(),
            None,
        )
        for n in (1, 2)
    ]
    assert ticket(issue(lane, "b", effect="up")).attempt == 1  # another path starts again
    lane.confirm(
        ticket(issue(lane, "a", effect="up")),
        lf.Confirmation(lf.ConfirmationStatus.NOT_APPLIED, "x", None),
    )
    assert ticket(issue(lane, "a", effect="down")).attempt == 1  # another effect starts again


def test_attempts_spent_refused_at_max_attempts(tmp_path: Path) -> None:
    lane = make_lane(tmp_path)
    port = Port()
    for expected in (1, 2):
        assert ticket(issue(lane, "svc", max_attempts=2, port=port)).attempt == expected
    before = lane_bytes(tmp_path)
    assert issue(lane, "svc", max_attempts=2, port=port) is TicketRefusal.ATTEMPTS_SPENT
    assert port.calls == 2 and lane_bytes(tmp_path) == before  # refused, nothing written
    # a remedy ticket is a ticket: it counts against the same bound (V-4.7)
    assert issue(lane, "svc", max_attempts=2, remedy=sup.REMEDY) is TicketRefusal.ATTEMPTS_SPENT


def test_once_already_issued_refused_first(tmp_path: Path) -> None:
    lane = make_lane(tmp_path)
    first = ticket(issue(lane, "svc", effect="submit", repeat=lf.Repeat.ONCE))
    assert (
        issue(lane, "svc", effect="submit", repeat=lf.Repeat.ONCE)
        is TicketRefusal.ONCE_ALREADY_ISSUED
    )  # unresolved: it may have taken effect (checked before IN_DOUBT)
    lane.confirm(first, lf.Confirmation(lf.ConfirmationStatus.NOT_APPLIED, "refused", None))
    second = ticket(issue(lane, "svc", effect="submit", repeat=lf.Repeat.ONCE))
    assert second.attempt == 2  # a NOT_APPLIED ticket never took effect
    lane.confirm(second, applied("s"))
    assert (
        issue(lane, "svc", effect="submit", repeat=lf.Repeat.ONCE, max_attempts=9)
        is TicketRefusal.ONCE_ALREADY_ISSUED
    )


def test_in_doubt_refuses_other_effect_safe_attempt_supersedes(tmp_path: Path) -> None:
    lane = make_lane(tmp_path)
    port = Port()
    first = ticket(issue(lane, "svc", effect="up", port=port))  # unconfirmed: unresolved
    assert issue(lane, "svc", effect="other", port=port) is TicketRefusal.IN_DOUBT
    assert port.calls == 1
    second = ticket(
        issue(lane, "svc", effect="up", port=port)
    )  # V-4.7: supersedes, same SAFE effect
    assert (first.attempt, second.attempt) == (1, 2)
    tickets = lane.node_record(("svc",)).tickets
    assert [t.attempt for t in tickets] == [1, 2]  # the earlier stays in the record, unconfirmed
    assert all(t.confirmation is None for t in tickets)
    # UNKNOWN is unresolved too; NOT_APPLIED resolves
    lane.confirm(second, lf.Confirmation(lf.ConfirmationStatus.UNKNOWN, None, None))
    assert issue(lane, "svc", effect="other") is TicketRefusal.IN_DOUBT
    third = ticket(issue(lane, "svc", effect="up"))
    lane.confirm(third, lf.Confirmation(lf.ConfirmationStatus.NOT_APPLIED, "refused", None))
    assert ticket(issue(lane, "svc", effect="other")).attempt == 1


@pytest.mark.proves(
    "WR-EVID-1",
    "WR-EVID-1:lane-unavailable-stops-before-effect",
    "A",
    "single",
    "PROC+MCP",
    "CI",
)
def test_lane_unavailable_stops_before_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail closed (F-11(a) carried; shape evidence, never a claim): a lane that cannot make the
    issue entry durable returns no ticket, so the facet never reaches its port."""
    port = Port()
    lane = make_lane(tmp_path)
    ticket(issue(lane, "svc", effect="warm", port=port))
    before = lane_bytes(tmp_path)
    assert port.calls == 1

    def failing_fsync(fd: int) -> None:
        raise OSError("injected fsync failure")

    with monkeypatch.context() as patch:
        patch.setattr(os, "fsync", failing_fsync)
        refusal = issue(lane, "svc", effect="warm", port=port)
    assert refusal is TicketRefusal.LANE_UNAVAILABLE
    assert port.calls == 1  # zero machine calls for the refused issue
    assert lane_bytes(tmp_path) == before  # not durable, so not in the lane
    assert len(lane.node_record(("svc",)).tickets) == 1
    # the lane recovers: the next issue is numbered after the one that is really there
    assert ticket(issue(lane, "svc", effect="warm", port=port)).attempt == 2 and port.calls == 2

    def broken(path: Path, record: dict[str, Any]) -> None:
        raise OSError("injected append failure")

    dead = make_lane(tmp_path, run_id="run-dead", append=broken, plan=False)
    with pytest.raises(OSError):
        dead.record_plan(sup.plan_entry(0).plan)  # a plan that cannot be made durable raises
    assert lane_bytes(tmp_path, "run-dead") == b""


def test_full_lane_refuses_issue_no_ticket(tmp_path: Path) -> None:
    lane = make_lane(tmp_path, entries=7)  # plan 1 + end 1 + one ticket 3 + 2 free
    port = Port()
    first = ticket(issue(lane, "svc", effect="up", port=port))
    before = lane_bytes(tmp_path)
    assert issue(lane, "svc", effect="up", port=port) is TicketRefusal.LANE_UNAVAILABLE
    assert port.calls == 1 and lane_bytes(tmp_path) == before  # no ticket, 0 spy port calls
    lane.confirm(first, applied("s"))  # earlier tickets' follow-ups still write
    assert isinstance(
        lf.read_lane(lf.lane_path(tmp_path / sup.ROOT)).entries[-1], lf.ConfirmationEntry
    )
    # ... and the refusal is not per node: the whole root's lane is full (V-4.7)
    assert issue(lane, "other", effect="up") is TicketRefusal.LANE_UNAVAILABLE


def test_ticket_follow_ups_write_into_held_slots(tmp_path: Path) -> None:
    lane = make_lane(tmp_path, entries=7)
    created = ticket(issue(lane, "svc", release=sup.ARGV))
    for n in range(2):  # fill every unreserved slot
        assert lane.record_step(sup.step_entry("svc", code=f"filler-{n}")) is None
    assert lane.record_step(sup.step_entry("svc", code="one-too-many")) is lf.LaneRefusal.FULL
    handle = lane.confirm(created, applied("sel"))  # reserved: never FULL
    assert handle is not None
    lane.record_released(handle, None)  # reserved: never FULL
    assert lane.record_end(sup.node_end()) is None
    read = lf.read_lane(lf.lane_path(tmp_path / sup.ROOT))
    assert len(read.entries) == 7  # the file never holds more than lane_entries entries


def test_step_on_full_lane_refused_full_writes_nothing(tmp_path: Path) -> None:
    lane = make_lane(tmp_path, entries=3)  # plan + end reserved, one free
    assert lane.record_step(sup.step_entry(code="one")) is None
    before = lane_bytes(tmp_path)
    assert lane.record_step(sup.step_entry(code="two")) is lf.LaneRefusal.FULL
    assert lane_bytes(tmp_path) == before
    over = sup.step_entry(code="x", human_action="h" * (bounds.HUMAN_ACTION_MAX + 1))
    fresh = make_lane(tmp_path, run_id="run-b", entries=9)
    assert fresh.record_step(over) is lf.LaneRefusal.OVER_BOUND  # nothing written, nothing cut
    assert len(lane_bytes(tmp_path, "run-b").split(b"\n")) == 2  # only the plan line


def test_record_end_into_reserved_slot_on_full_lane(tmp_path: Path) -> None:
    lane = make_lane(tmp_path, entries=7, scope=(ROOT_PATH,))
    created = ticket(issue(lane, effect="up", release=sup.ARGV))
    lane.confirm(created, applied("s"))
    while lane.record_step(sup.step_entry(code="fill")) is None:
        pass  # the lane is now full of everything but its reservations
    assert lane.record_step(sup.step_entry(code="refused")) is lf.LaneRefusal.FULL
    end = sup.node_end(condition=lf.Condition.FAILED, code="unit.failed")
    assert lane.record_end(end) is None  # into the reserved slot, never FULL
    read = lf.read_lane(lf.lane_path(tmp_path / sup.ROOT))
    assert isinstance(read.entries[-1], lf.NodeEnd) and read.entries[-1].code == "unit.failed"


def test_second_record_end_duplicate_first_stands(tmp_path: Path) -> None:
    lane = make_lane(tmp_path, scope=(("a",), ("b",)))
    first = sup.node_end("a", code="first", condition=lf.Condition.FAILED)
    assert lane.record_end(first) is None
    before = lane_bytes(tmp_path)
    second = sup.node_end("a", code="second", condition=lf.Condition.BLOCKED)
    assert lane.record_end(second) is lf.LaneRefusal.DUPLICATE
    assert lane_bytes(tmp_path) == before  # nothing written, the first stands
    ends = [
        e
        for e in lf.read_lane(lf.lane_path(tmp_path / sup.ROOT)).entries
        if isinstance(e, lf.NodeEnd)
    ]
    assert [e.code for e in ends] == ["first"]
    assert lane.record_end(sup.node_end("b")) is None  # another vertex has its own slot


def test_record_end_unavailable_leaves_vertex_unended(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lane = make_lane(tmp_path)
    before = lane_bytes(tmp_path)
    with monkeypatch.context() as patch:
        patch.setattr(os, "fsync", lambda fd: (_ for _ in ()).throw(OSError("injected")))
        assert lane.record_end(sup.node_end()) is lf.LaneRefusal.UNAVAILABLE
    assert lane_bytes(tmp_path) == before  # the vertex has no NodeEnd
    entries = lf.read_lane(lf.lane_path(tmp_path / sup.ROOT)).entries
    assert not any(isinstance(e, lf.NodeEnd) for e in entries)
    assert lane.record_end(sup.node_end()) is None  # its slot is still reserved: a retry lands


def test_one_vertex_two_unconfirmed_handles_node_end_written(tmp_path: Path) -> None:
    lane = make_lane(tmp_path, entries=8)  # plan + end + two tickets x 3: exactly at capacity
    for _ in range(2):
        ticket(issue(lane, effect="up", release=sup.ARGV))
    assert issue(lane, effect="up") is TicketRefusal.LANE_UNAVAILABLE  # at capacity
    assert (
        lane.record_end(
            sup.node_end(condition=lf.Condition.IN_DOUBT, provenance=lf.Provenance.CLAIMED)
        )
        is None
    )
    (a, b) = lane.node_record(ROOT_PATH).tickets
    assert a.confirmation is None and b.confirmation is None  # both listed, unconfirmed
    assert a.handle is None and b.handle is None
    entries = lf.read_lane(lf.lane_path(tmp_path / sup.ROOT)).entries
    assert isinstance(entries[-1], lf.NodeEnd)
    assert not any(isinstance(e, lf.ConfirmationEntry) for e in entries)


def test_capacity_below_plan_and_vertex_slots_raises(tmp_path: Path) -> None:
    scope = (("a",), ("b",), ("c",))
    with pytest.raises(ValueError):
        AttemptLane(tmp_path / "run-a", len(scope), scope)  # < |scope| + 1
    AttemptLane(tmp_path / "run-b", len(scope) + 1, scope)  # the least that fits


def test_selection_releases_the_unselected_alternatives_slots(tmp_path: Path) -> None:
    """V-4.8: `V_run` omits the alternatives the recorded selection did not select, so their
    `record_end` slots are not reserved."""
    scope = ((), ("c",), ("c", "x"), ("c", "y"), ("c", "y", "leaf"))
    lane = AttemptLane(tmp_path / sup.ROOT, len(scope) + 1, scope)
    plan = lf.PlanIdentity("d", "a", {("c",): ("c", "x")}, "o")
    lane.record_plan(plan)
    # 6 entries in all: plan + 3 NodeEnds reserved (root, c, c/x); two slots are free
    assert lane.record_step(sup.step_entry("c", code="a")) is None
    assert lane.record_step(sup.step_entry("c", code="b")) is None
    assert lane.record_step(sup.step_entry("c", code="c")) is lf.LaneRefusal.FULL
    for path in ((), ("c",), ("c", "x")):
        assert lane.record_end(sup.node_end(*path)) is None
