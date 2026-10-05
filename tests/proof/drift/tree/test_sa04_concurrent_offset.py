"""SA-04 in A-2 (L.TR-3.2): the flag-then-offset stop protocol holds while several nodes append.

At one vertex one appender wrote the lane. A tree walks its leaves on threads, all through the one
serialized appender (B2-C7), so the offset proof of `tests/proof/spine/test_stop_offset.py` is
re-run over a record several nodes wrote: three leaves create their marker (applied, before the
flag), then poll; the stop flag goes up, the lane's committed length is read (what U2 does, B2-C15),
and the run ends. Read afterwards, with nothing of the run alive, no APPLIED non-release entry lies
past the offset in any path (only each node's release does), and a planted second appender, whose
late write is applied past the offset or reuses a sequence number, is caught.

The in-library form here has no U2 and no stop row: the offset is read at the flag exactly as U2
reads it. The process form of the same claim over a walking tree is `L.TR-3.6`'s
(`readiness_sibling`, held) and `L.TR-L.8`'s (host)."""

from __future__ import annotations

import json
import shutil
import threading
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from tests.proof import records, tolerances
from tests.proof.records import LaneRows
from tests.proof.spine import test_stop_offset as suite
from tests.single.workflow import loopkit as kit
from tests.tree import treekit as tk
from trestle.workflow.values import StopCause

LEAVES = ("a", "b", "c")
RELEASES = frozenset({kit.STOP_EFFECT})


def _copy_lane(run_dir: Path, tmp_path: Path, extra: Sequence[dict[str, Any]] = ()) -> LaneRows:
    target = tmp_path / f"copy-{len(list(tmp_path.iterdir()))}"
    (target / "evidence").mkdir(parents=True)
    shutil.copy(run_dir / "evidence" / "lane.ndjson", target / "evidence" / "lane.ndjson")
    if extra:
        with (target / "evidence" / "lane.ndjson").open("a", encoding="utf-8") as lane:
            for entry in extra:
                lane.write(json.dumps(entry, separators=(",", ":")) + "\n")
    return records.lane_rows(target)


def _late_apply(lane: LaneRows, path: str, seq: int, attempt: int = 2) -> list[dict[str, Any]]:
    """A second writer's claim and applied create for `path`, after the offset."""
    (issue,) = [
        r.entry
        for r in lane.rows
        if r.cls == "issue" and r.path == path and r.entry["effect"] == tk.EFFECT
    ]
    (confirmation,) = [
        r.entry
        for r in lane.rows
        if r.cls == "confirmation" and r.path == path and r.entry["effect"] == tk.EFFECT
    ]
    return [
        {**issue, "seq": seq, "attempt": attempt},
        {**confirmation, "seq": seq + 1, "attempt": attempt},
    ]


@pytest.mark.parametrize("sa", ["SA-04"])
def test_offset_sound_with_concurrent_nodes(sa: str, tmp_path: Path) -> None:
    polling = threading.Barrier(len(LEAVES) + 1, timeout=tolerances.JOIN_WAIT_S)
    gate = threading.Event()

    def _arrived(path: str) -> None:
        polling.wait()

    root = tk.group("app", tuple(tk.bind(n) for n in LEAVES), concurrency=len(LEAVES))
    rig = tk.tree_rig(
        tmp_path / "run", root, {n: tk.held_unit(n, gate, on_hold=_arrived) for n in LEAVES}
    )
    runner = threading.Thread(target=rig.run)
    runner.start()
    try:
        polling.wait()  # every leaf created (applied) and is inside its poll
        rig.rig.cancel.stop = StopCause.CANCEL  # the flag first ...
        offset = rig.rig.services.attempts().committed_length()  # ... then the offset (B2-C15)
    finally:
        gate.set()
        runner.join(tolerances.JOIN_WAIT_S)
    assert not runner.is_alive()  # the run is over: the record is all there is

    lane = records.lane_rows(rig.run_dir)
    assert not lane.problems and not lane.torn, lane.problems
    stop = {"cause": "cancel", "lane_committed_length": offset}
    verdict = suite.offset_verdict(lane, [stop], RELEASES)
    assert verdict.ok and not verdict.vacuous and not verdict.unproven, verdict

    # not a vacuous pass: every node applied its create before the offset, from one appender, and
    # every release lies past it
    creates = [r for r in lane.rows if r.cls == "confirmation" and r.entry["effect"] == tk.EFFECT]
    assert {r.path for r in creates} == set(LEAVES)
    assert all(r.entry["status"] == "applied" and r.end <= offset for r in creates)
    releases = [r for r in lane.rows if r.cls == "confirmation" and r.entry["effect"] == "stop"]
    assert {r.path for r in releases} == set(LEAVES)
    assert all(r.offset >= offset for r in releases)
    seqs = [r.entry["seq"] for r in lane.rows]
    assert seqs == sorted(set(seqs)), "one serialized appender: sequence numbers strictly increase"

    # a second appender (a planted concurrent write) is caught, by its offset or its sequence
    last = lane.rows[-1].entry["seq"]
    late = _copy_lane(rig.run_dir, tmp_path, _late_apply(lane, "b", last + 1))
    assert not suite.offset_verdict(late, [stop], RELEASES).ok
    clash = _copy_lane(rig.run_dir, tmp_path, _late_apply(lane, "b", last - 1))
    assert clash.problems and not suite.offset_verdict(clash, [stop], RELEASES).ok
    ends = {r.path: r.entry for r in lane.rows if r.cls == "end"}
    assert all(ends[p]["cut"] == "stopped" for p in LEAVES)  # cut by the stop, none passed
