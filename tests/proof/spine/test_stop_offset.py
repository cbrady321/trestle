"""The stop-offset record-fact sub-suite (L.SV-5.12; SA-04, SA-05): the flag-then-offset stop
protocol proven from records with the plugin dead (MC-21, MC-26, MC-10).

U2 reads the lane's committed length and only then appends the one `stop_row(cause,
lane_committed_length)` to the ledger (B2-C15); the flag already existed, so every entry the lane
holds past that length was appended after the flag, and a non-release ticket among them was
recorded not applied (`STOP_SEEN`, B1-C6 step 3a). The suite reads a stopped run's directory after
the wrapper and the child have exited and proves, from the record alone:

* exactly one stop row, and the run's class is the one its cause names (B2-C15, B4-C2): `cancel`
  -> `cancelled`, `release_point` -> `timed_out`;
* no APPLIED, non-release entry lies past `lane_committed_length`; a `STOP_SEEN` entry is not an
  action start and a release is allowed (it is the loop giving back what it made);
* a stop row whose length is `None` is reported unproven, never passed;
* the request path signalled nothing (a spy on `os.kill` / `os.killpg` sees no call on the
  requesting thread; U2's thread alone signals, B2-C10);
* a planted 'applied past the offset' and a planted concurrent write are caught.

The two real runs are the harness's one-vertex fixture with a marker that never turns ready: a
cancel mid-poll and the deadline mid-poll. Every timing bound is the harness's or the published
clock's (SA-05); no timing literal appears here.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tests.core.spine import support
from tests.proof import harness, record_facts, records, tolerances
from tests.proof.record_facts import Verdict
from tests.proof.records import LaneRows
from trestle.common import clock

pytestmark = pytest.mark.spine

REPO = Path(__file__).resolve().parents[3]
FIXTURE = REPO / "tests" / "fixtures" / "workflows" / "spine_leaf.py"
# the fixture's declared deadline (decorator and entry) and the shorter one that lets the deadline
# end the wait: the root budget plus the release slice must fit it (the same as the W-A1 variant)
DECLARED_DEADLINE_S = 120
SHORT_DEADLINE_S = 20
CLASS_OF_CAUSE = {"cancel": "cancelled", "release_point": "timed_out"}
HANG = {"env": "dev", "mode": "hang"}
STALL = {"env": "dev", "mode": "stall"}  # hang, whose wait starts late: the deadline ends it


# ---- the facts --------------------------------------------------------------------------------


def release_effects(run_dir: Path) -> frozenset[str]:
    """The effects the run's declaration marks `is_release` (read from the snapshot's declaration
    record, `snapshots/<id>/declaration.json`): a release past the offset is allowed."""
    spec = json.loads((run_dir / "evidence" / "spec.json").read_text(encoding="utf-8"))
    home = run_dir.parents[2]
    declaration = json.loads(
        (home / "snapshots" / spec["snapshot_id"] / "declaration.json").read_text(encoding="utf-8")
    )
    effects = declaration["nodes"][""]["effects"]
    return frozenset(e["effect"] for e in effects if e["is_release"])


def stop_rows_of(run_dir: Path) -> list[dict[str, Any]]:
    return [r for r in records.ledger_rows(run_dir).rows if r.get("kind") == "stop_row"]


def offset_verdict(
    lane: LaneRows, stop_rows: Sequence[Mapping[str, Any]], releases: frozenset[str]
) -> Verdict:
    """The offset proof over one run's record. Not vacuous (an empty lane proves nothing), never
    a pass on an unreadable lane (a seq that does not increase is a second appender, SA-04) or on a
    length U2 could not read (unproven)."""
    if not lane.rows:
        return Verdict(False, ("the lane holds no entry: nothing is proven",), vacuous=True)
    if lane.problems or lane.torn:
        return Verdict(False, tuple(lane.problems) or ("the lane is torn",))
    if len(stop_rows) != 1:
        return Verdict(False, (f"{len(stop_rows)} stop rows, not exactly one",))
    length = stop_rows[0]["lane_committed_length"]
    if length is None:
        return record_facts.applied_past_offset(lane, None)  # unproven, never passed
    keyed = {
        (t["issue"].path or "", t["issue"].entry["effect"], t["issue"].entry["attempt"]): t
        for t in records.lane_tickets(lane)
    }
    violations = []
    for row in lane.rows:
        if row.cls != "confirmation" or row.entry["status"] != "applied" or row.offset < length:
            continue
        if row.entry["effect"] in releases:
            continue  # a release is the loop giving back what it made (B2-C15)
        assert (row.path or "", row.entry["effect"], row.entry["attempt"]) in keyed
        violations.append(
            f"APPLIED {row.entry['effect']} at byte {row.offset} is past offset {length}"
        )
    return Verdict(not violations, tuple(violations))


# ---- the real runs ----------------------------------------------------------------------------


@dataclass
class Stopped:
    run_dir: Path
    outcome_class: str
    answer_outcome: str
    request_calls: list[tuple[str, int]] = field(
        default_factory=list
    )  # signals on the request thread

    @property
    def lane(self) -> LaneRows:
        return records.lane_rows(self.run_dir)

    @property
    def stops(self) -> list[dict[str, Any]]:
        return stop_rows_of(self.run_dir)

    def verdict(self) -> Verdict:
        return offset_verdict(self.lane, self.stops, release_effects(self.run_dir))


def _observations(run_dir: Path) -> int:
    events = run_dir / "evidence" / "events.ndjson"
    return len(events.read_text(encoding="utf-8").splitlines()) if events.exists() else 0


def _action_confirmed(run_dir: Path) -> bool:
    """The fixture's non-release action has its APPLIED confirmation in the lane. Cancelling only
    after this puts the cancel mid-poll, never between a ticket's stop check (B1-C6 step 3a) and
    its confirmation (step 5); the observation count alone is reached before the action starts."""
    releases = release_effects(run_dir)
    return any(
        row.cls == "confirmation"
        and row.entry["status"] == "applied"
        and row.entry["effect"] not in releases
        for row in records.lane_rows(run_dir).rows
    )


class SignalSpy:
    """Every os.kill / os.killpg made in this process, with the calling thread's name."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, int, str]] = []
        self._patch = pytest.MonkeyPatch()

    def __enter__(self) -> SignalSpy:
        real_kill, real_killpg = os.kill, os.killpg

        def kill(pid: int, signum: int) -> None:
            self.calls.append(("kill", int(signum), threading.current_thread().name))
            real_kill(pid, signum)

        def killpg(pgid: int, signum: int) -> None:
            self.calls.append(("killpg", int(signum), threading.current_thread().name))
            real_killpg(pgid, signum)

        self._patch.setattr(os, "kill", kill)
        self._patch.setattr(os, "killpg", killpg)
        return self

    def __exit__(self, *exc: object) -> None:
        self._patch.undo()


def _answered(view: Any) -> tuple[str, str]:
    wire = view.to_dict()
    return str(wire["outcome"]["class"]), str(wire["answer"]["outcome"])


def run_cancelled() -> Stopped:
    """The fixture with a marker that never turns ready, cancelled mid-poll through the control
    surface; the wait is over when the conductor returns."""
    with SignalSpy() as spy:
        admitted = harness.admit_tree(FIXTURE, HANG)
        views: list[Any] = []
        conductor = threading.Thread(target=lambda: views.append(harness.drive_tree(admitted)))
        conductor.start()
        with support.reaping(admitted.run_id):
            assert support.wait_until(
                lambda: (
                    _action_confirmed(admitted.run_dir) and _observations(admitted.run_dir) >= 3
                ),
                tolerances.JOIN_WAIT_S,
            ), "the wait never polled"
            spy.calls.clear()
            admitted.kernel.control.cancel(admitted.run_id)  # the request path
            asked = threading.current_thread().name
            request_calls = [(k, s) for k, s, name in spy.calls if name == asked]
            conductor.join(timeout=clock.stop_bound + tolerances.JOIN_WAIT_S)
            assert not conductor.is_alive(), "the run never reached its terminal row"
    outcome, answer = _answered(views[0])
    return Stopped(admitted.run_dir, outcome, answer, request_calls)


def run_deadline(directory: Path) -> Stopped:
    """The same fixture under a short deadline; the release point ends the wait."""
    source = FIXTURE.read_text(encoding="utf-8")
    source = source.replace(f"deadline={DECLARED_DEADLINE_S}", f"deadline={SHORT_DEADLINE_S}")
    source = source.replace(f"seconds={DECLARED_DEADLINE_S}", f"seconds={SHORT_DEADLINE_S}")
    directory.mkdir(parents=True, exist_ok=True)
    fixture = directory / FIXTURE.name
    fixture.write_text(source, encoding="utf-8")
    admitted = harness.admit_tree(fixture, STALL)
    with support.reaping(admitted.run_id):
        view = harness.drive_tree(admitted)
    outcome, answer = _answered(view)
    return Stopped(admitted.run_dir, outcome, answer)


@pytest.fixture(scope="module")
def cancelled() -> Stopped:
    return run_cancelled()


@pytest.fixture(scope="module")
def timed_out(tmp_path_factory: pytest.TempPathFactory) -> Stopped:
    return run_deadline(tmp_path_factory.mktemp("deadline-plugin"))


# ---- what the record says ---------------------------------------------------------------------


@pytest.mark.proves(
    "WR-CANCEL-1",
    "WR-CANCEL-1:A-no-applied-effect-past-stop-offset",
    "A",
    "single",
    "PROC",
    "BOTH",
)
def test_cancel_one_stop_row_class_is_its_cause(cancelled: Stopped) -> None:
    (stop,) = cancelled.stops
    assert stop["cause"] == "cancel"
    assert cancelled.outcome_class == cancelled.answer_outcome == CLASS_OF_CAUSE[stop["cause"]]
    verdict = cancelled.verdict()
    assert verdict.ok and not verdict.vacuous and not verdict.unproven, verdict


def test_deadline_one_stop_row_class_is_its_cause(timed_out: Stopped) -> None:
    (stop,) = timed_out.stops
    assert stop["cause"] == "release_point"
    assert timed_out.outcome_class == timed_out.answer_outcome == CLASS_OF_CAUSE[stop["cause"]]
    verdict = timed_out.verdict()
    assert verdict.ok and not verdict.vacuous and not verdict.unproven, verdict


# The record's own spelling of `StepEntry(FAILED, CARVE_EXCEEDED)`: this suite reads the record
# through the seam and imports no product vocabulary but the clock (SA-04).
CARVE_EXCEEDED_STEP = ("failed", "execution.carve_exceeded")


def _allowed_past_offset(row: Any) -> bool:
    """What may lie past the stop offset: the release walk (the stop issue and its confirmation,
    `released`), the `end`, and the leaf's `StepEntry(FAILED, CARVE_EXCEEDED)` (loop
    `_carve_exceeded`), no other step."""
    if row.cls in ("released", "end"):
        return True
    if row.cls == "step":
        return (row.entry.get("kind"), row.entry.get("code")) == CARVE_EXCEEDED_STEP
    return row.entry.get("effect") == "stop"


def test_only_the_carve_exceeded_step_may_lie_past_the_offset() -> None:
    def row(cls: str, **entry: Any) -> SimpleNamespace:
        return SimpleNamespace(cls=cls, entry=entry)

    carve = row("step", kind="failed", code="execution.carve_exceeded")
    assert _allowed_past_offset(carve)
    assert not _allowed_past_offset(row("step", kind="blocked", code="execution.carve_exceeded"))
    assert not _allowed_past_offset(row("step", kind="failed", code="execution.unit_raised"))
    assert not _allowed_past_offset(row("issue", effect="up"))
    assert _allowed_past_offset(row("issue", effect="stop"))


@pytest.mark.parametrize("which", ["cancelled", "timed_out"])
def test_the_offset_separates_the_claim_from_the_release(
    which: str, request: pytest.FixtureRequest
) -> None:
    """Not a vacuous pass: the create was applied before the recorded offset, and the release
    walk (the stop issue, its confirmation and `released`) lies past it, all allowed. A `step`
    may lie past it too, but only that one record: under the deadline the leaf's own slice ends at
    the release point, and its `StepEntry(FAILED, CARVE_EXCEEDED)` can land after the stop row; a
    step is a record, not an action start (it has no effect). Any other step past the offset is
    still a finding."""
    run: Stopped = request.getfixturevalue(which)
    (stop,) = run.stops
    length = stop["lane_committed_length"]
    rows = run.lane.rows
    (create,) = [r for r in rows if r.cls == "confirmation" and r.entry["effect"] == "up"]
    assert create.entry["status"] == "applied" and create.end <= length
    past = [r for r in rows if r.offset >= length]
    assert [r.cls for r in past if r.cls in ("issue", "confirmation", "released")], past
    assert all(_allowed_past_offset(r) for r in past), past


def test_request_path_emitted_no_signal(cancelled: Stopped) -> None:
    """The cancel request wrote a flag and nothing else: no signal left the requesting thread."""
    assert cancelled.request_calls == []


# ---- the plugin is dead -----------------------------------------------------------------------


def test_read_after_the_run_with_the_plugin_dead(cancelled: Stopped) -> None:
    assert not support.marked(cancelled.run_dir.name), "a process of the run is alive"


# ---- planted defects --------------------------------------------------------------------------


def _copy_lane(run: Stopped, tmp_path: Path, extra: Sequence[dict[str, Any]] = ()) -> LaneRows:
    """A copy of the run's lane file with `extra` entries appended (a defect planted after the
    fact); read through the oracle."""
    target = tmp_path / f"copy-{len(list(tmp_path.iterdir()))}"
    (target / "evidence").mkdir(parents=True)
    shutil.copy(run.run_dir / "evidence" / "lane.ndjson", target / "evidence" / "lane.ndjson")
    if extra:
        with (target / "evidence" / "lane.ndjson").open("a", encoding="utf-8") as lane:
            for entry in extra:
                lane.write(json.dumps(entry, separators=(",", ":")) + "\n")
    return records.lane_rows(target)


def _late_apply(run: Stopped, *, seq: int, attempt: int = 2) -> list[dict[str, Any]]:
    """The entries of a writer that claims and applies a new create after the offset."""
    (issue,) = [r.entry for r in run.lane.rows if r.cls == "issue" and r.entry["effect"] == "up"]
    late_issue = {**issue, "seq": seq, "attempt": attempt}
    (confirmation,) = [
        r.entry for r in run.lane.rows if r.cls == "confirmation" and r.entry["effect"] == "up"
    ]
    return [late_issue, {**confirmation, "seq": seq + 1, "attempt": attempt}]


def _late_release(
    run: Stopped, releases: frozenset[str], *, seq: int, attempt: int = 2
) -> list[dict[str, Any]]:
    """The entries of the loop issuing and applying a release again after the offset."""
    (issue,) = [
        r.entry for r in run.lane.rows if r.cls == "issue" and r.entry["effect"] in releases
    ]
    (confirmation,) = [
        r.entry
        for r in run.lane.rows
        if r.cls == "confirmation" and r.entry["effect"] == issue["effect"]
    ]
    return [
        {**issue, "seq": seq, "attempt": attempt},
        {**confirmation, "seq": seq + 1, "attempt": attempt},
    ]


def test_planted_applied_past_offset_is_caught(cancelled: Stopped, tmp_path: Path) -> None:
    (stop,) = cancelled.stops
    releases = release_effects(cancelled.run_dir)
    lane = _copy_lane(cancelled, tmp_path)
    assert offset_verdict(lane, [stop], releases).ok  # the real record passes
    # the same lane with a length that lies before the create's confirmation
    (create,) = [r for r in lane.rows if r.cls == "confirmation" and r.entry["effect"] == "up"]
    lying = {**stop, "lane_committed_length": create.offset}
    caught = offset_verdict(lane, [lying], releases)
    assert not caught.ok and "APPLIED up" in caught.violations[0], caught


def test_planted_concurrent_write_is_caught(cancelled: Stopped, tmp_path: Path) -> None:
    (stop,) = cancelled.stops
    releases = release_effects(cancelled.run_dir)
    last = cancelled.lane.rows[-1].entry["seq"]
    # (a) a second appender applies an effect after U2 read the length
    after = _copy_lane(cancelled, tmp_path, _late_apply(cancelled, seq=last + 1))
    caught = offset_verdict(after, [stop], releases)
    assert not caught.ok and "APPLIED up" in caught.violations[-1], caught
    # (b) two appenders interleave: a seq that does not increase, whatever the offset says
    clash = _copy_lane(cancelled, tmp_path, _late_apply(cancelled, seq=last - 1))
    assert clash.problems
    assert not offset_verdict(clash, [stop], releases).ok


def test_stop_seen_and_releases_past_the_offset_are_not_counted(
    cancelled: Stopped, tmp_path: Path
) -> None:
    (stop,) = cancelled.stops
    releases = release_effects(cancelled.run_dir)
    last = cancelled.lane.rows[-1].entry["seq"]
    seen = _late_apply(cancelled, seq=last + 1)
    seen[1] = {**seen[1], "status": "not_applied", "code": "execution.stop_seen", "identity": None}
    lane = _copy_lane(cancelled, tmp_path, seen)
    assert offset_verdict(lane, [stop], releases).ok  # a STOP_SEEN is not an action start
    # ... and an applied release past the offset is not counted. It is planted: the real record's
    # own release lies past the offset only when U2 read the length before the loop released, and
    # both react to the same flag with no order between them (B2-C15, B1-C6)
    assert releases
    released = _copy_lane(cancelled, tmp_path, _late_release(cancelled, releases, seq=last + 1))
    planted = [r for r in released.rows if r.entry["seq"] == last + 2]
    assert [r.cls for r in planted] == ["confirmation"]
    assert planted[0].entry["status"] == "applied"
    assert planted[0].offset >= stop["lane_committed_length"]
    assert offset_verdict(released, [stop], releases).ok
    # the release exemption is what passes it: counted as an action, the same entry is caught
    counted = offset_verdict(released, [stop], frozenset())
    assert not counted.ok and "APPLIED" in counted.violations[-1], counted
    # ... and the real record's own release, when it does lie past the offset, is not counted
    if any(
        r.cls == "confirmation"
        and r.entry["effect"] in releases
        and r.offset >= stop["lane_committed_length"]
        for r in cancelled.lane.rows
    ):
        assert offset_verdict(cancelled.lane, [stop], releases).ok


def test_a_length_that_could_not_be_read_is_unproven_never_passed(
    cancelled: Stopped, tmp_path: Path
) -> None:
    (stop,) = cancelled.stops
    lane = _copy_lane(cancelled, tmp_path)
    unproven = offset_verdict(lane, [{**stop, "lane_committed_length": None}], frozenset())
    assert not unproven.ok and unproven.unproven and not unproven.vacuous


def test_the_suite_is_not_vacuous(tmp_path: Path) -> None:
    empty = records.lane_rows(tmp_path)
    assert offset_verdict(empty, [], frozenset()).vacuous


def test_exactly_one_stop_row_is_required(cancelled: Stopped, tmp_path: Path) -> None:
    (stop,) = cancelled.stops
    lane = _copy_lane(cancelled, tmp_path)
    releases = release_effects(cancelled.run_dir)
    assert not offset_verdict(lane, [], releases).ok
    assert not offset_verdict(lane, [stop, stop], releases).ok
