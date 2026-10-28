"""L.TR-L.8: root cancel and root deadline through the host (WR-UNIT-6; A6.3).

`readiness_sibling` (L.TR-3.6) is a root over a waiter and a branch of two more, every leaf
creating a marker and then polling a readiness that never comes. A run of it goes through the
MCP `run` tool of a real `trestle serve` (MC-12), never MC-26 (`tests/tree/hostpath.py` names the
entry points); the same tree is read after its terminal row with the plugin dead:

* cancel: one MCP `cancel` while one child polls and a sibling branch runs;
* deadline: the same tree under a short root deadline, whose leaves wait cooperatively past the
  release point, so the deadline (not a slice or a wait) is what ends the run.

Each run must show the four things the clauses name: no applied non-release lane entry past the
stop offset in any path (the SA-04 offset proof, with the plugin dead), every attributable pid gone
within the published stop bound (MC-13 snapshot before, snapshot after), every resource the run
created released and a resource it only found left as it was (the fake's inventory and the found
marker's bytes), and a terminal answer of the class the cause names. Every timing bound is
`tests/proof/tolerances.py` or `trestle.common.clock` (SA-05); the deadline variant edits the
fixture's own constants, which are the tree's declared budgets, not test timings."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from trestle_packs.fakes import FakeMarker

from tests.proof import ancestry, mcp_host, records, tolerances
from tests.proof.spine import test_stop_offset as stop_offset
from tests.tree import hostpath
from trestle.common import clock, codes

TREES = Path(__file__).resolve().parents[2] / "fixtures" / "trees"
FIXTURE = "readiness_sibling"
LEAVES = ("waiter", "branch/w1", "branch/w2")
LEAF_UNITS = ("waiter", "w1", "w2")
HOST_TIMEOUT_S = tolerances.JOIN_WAIT_S * 6
MIN_MARKER = 8  # `support.marked`'s rule: a shorter argv marker would match unrelated processes

proves_cancel = pytest.mark.proves(
    "WR-UNIT-6", "WR-UNIT-6:cancel-no-start-after", "A", "tree", "PROC+MCP", "BOTH"
)
proves_pids = pytest.mark.proves(
    "WR-UNIT-6", "WR-UNIT-6:cancel-pids-dead", "A", "tree", "PROC+MCP", "BOTH"
)
proves_created = pytest.mark.proves(
    "WR-UNIT-6", "WR-UNIT-6:cancel-created-gone-found-untouched", "A", "tree", "PROC+MCP", "BOTH"
)
proves_deadline = pytest.mark.proves(
    "WR-UNIT-6", "WR-UNIT-6:deadline-variant", "A", "tree", "PROC+MCP", "BOTH"
)
proves_bound = pytest.mark.proves("WR-CANCEL-1", "WR-CANCEL-1:tree", "A", "tree", "PROC", "BOTH")
proves_a63 = pytest.mark.proves("WR-UNIT-6", "A6.3", "A", "tree", "PROC+MCP", "BOTH")

# The deadline variant's source edits. The tree's declared budgets nest by the reserve (leaf
# budget + reserve = branch, + reserve = root) and the deadline is the root budget plus the release
# slice plus a margin, so shortening it shortens every constant together. A wait's own end and the
# leaf's slice both fall before the release point (a child's slice ends a reserve before its
# parent's), so the second observation of each leaf holds cooperatively until the stop is raised:
# the run stays alive past every slice and only the root deadline (release point) ends it.
SHORT_DEADLINE_S = 38
SHORT = {
    "LEAF_BUDGET_S = 34": "LEAF_BUDGET_S = 6",
    "BRANCH_BUDGET_S = 44": "BRANCH_BUDGET_S = 16",
    "ROOT_BUDGET_S = 54": "ROOT_BUDGET_S = 26",
    "DEADLINE_S = 70": f"DEADLINE_S = {SHORT_DEADLINE_S}",
    "@trestle(deadline=70,": f"@trestle(deadline={SHORT_DEADLINE_S},",
    "timedelta(seconds=30)),": "timedelta(seconds=2)),",
    "        self._unit = unit\n": "        self._unit = unit\n        self._seen = 0\n",
    "        resource = reads.read(ResourceReads)\n": (
        "        self._seen += 1\n"
        "        if self._seen == 2:  # first poll, after the create: hold\n"
        "            ctx.cancellation.wait(timedelta(seconds=HOLD_S))\n"
        "        resource = reads.read(ResourceReads)\n"
    ),
    'STOP_EFFECT = "stop"\n': 'STOP_EFFECT = "stop"\nHOLD_S = 600\n',
}


def source(*, short: bool) -> str:
    text = (TREES / f"{FIXTURE}.py").read_text(encoding="utf-8")
    if not short:
        return text
    for old, new in SHORT.items():
        assert text.count(old) == 1, f"{FIXTURE}: {old!r} moved"
        text = text.replace(old, new)
    return text


@pytest.fixture
def mcp(tmp_path: Path) -> Any:
    with mcp_host.McpHost(home=tmp_path / "host-home", timeout_s=HOST_TIMEOUT_S) as host:
        yield host


def wait_until(predicate: Callable[[], bool], bound_s: float) -> bool:
    deadline = time.monotonic() + bound_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(tolerances.POLL_FINE_S)
    return predicate()


def run_dir_of(host: mcp_host.McpHost, run_id: str) -> Path:
    (found,) = sorted((host.home / "runs").glob(f"*/{run_id}"))
    return found


def release_effects(run_dir: Path) -> frozenset[str]:
    """Every effect any node of the run's declared tree marks `is_release`."""
    spec = json.loads((run_dir / "evidence" / "spec.json").read_text(encoding="utf-8"))
    declaration = json.loads(
        (run_dir.parents[2] / "snapshots" / spec["snapshot_id"] / "declaration.json").read_text(
            encoding="utf-8"
        )
    )
    return frozenset(
        e["effect"]
        for node in declaration["nodes"].values()
        for e in node.get("effects", ())
        if e["is_release"]
    )


def markers_of(run_dir: Path) -> Path:
    return run_dir / "work" / "tmp" / "markers"


def marker_records(run_dir: Path) -> dict[str, dict[str, Any]]:
    """Every marker file the run's fake holds: selector -> its record."""
    found: dict[str, dict[str, Any]] = {}
    for path in sorted(markers_of(run_dir).glob("*.marker")):
        try:
            found[path.name.removesuffix(".marker")] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue  # being written
    return found


def process_markers(run_dir: Path) -> dict[str, int]:
    return {s: r["pid"] for s, r in marker_records(run_dir).items() if r.get("kind") == "process"}


def _plain(proc: ancestry.ProcInfo) -> bool:
    return os.path.basename(proc.argv.split(" ", 1)[0]) != "ps"


def attributable(server_pid: int, run_id: str, pids: set[int]) -> set[ancestry.ProcInfo]:
    """Every live process attributable to the run (MC-13), by descent from the server, by the run
    id in its argv, or by being a marker process the run's fake started (a reparented one)."""
    assert len(run_id) >= MIN_MARKER
    table = [p for p in ancestry.snapshot() if _plain(p)]
    under: set[int] = set()
    frontier = {server_pid}
    while frontier:
        frontier = {p.pid for p in table if p.ppid in frontier} - under
        under |= frontier
    return {p for p in table if p.pid in under or p.pid in pids or run_id in p.argv}


@dataclass
class Stopped:
    """A run that was stopped, read after its terminal row."""

    run_id: str
    run_dir: Path
    view: dict[str, Any]
    before: set[ancestry.ProcInfo]  # attributable to the run just before the stop was raised
    marker_pids: set[int]
    created: dict[str, dict[str, Any]]  # the process markers the run made, by selector
    found_selector: str
    found_bytes: bytes


def _leaves_polling(run_dir: Path) -> bool:
    made = {
        r.path
        for r in records.lane_rows(run_dir).rows
        if r.cls == "confirmation" and r.entry["effect"] == "up" and r.entry["status"] == "applied"
    }
    return made >= set(LEAVES) and len(process_markers(run_dir)) == len(LEAVES)


def start_and_settle(host: mcp_host.McpHost, text: str) -> tuple[int, str, Path]:
    """Publish the fixture into the host and start a run through the MCP `run` tool as
    `hostpath.mcp_run_tree` does (`completion="terminal"`), held so a `cancel` can flow on the
    same connection; then wait until every leaf has created its marker and is polling. Returns the
    held request id, the run id and the run directory."""
    (host.home / "plugins" / f"{FIXTURE}.py").write_text(text, encoding="utf-8")
    request = host.hold(
        "run",
        {
            "plugin": FIXTURE,
            "args": {"env": "dev"},
            "wait_ms": hostpath.WAIT_MS,
            "completion": "terminal",
        },
    )
    assert wait_until(
        lambda: bool(list((host.home / "runs").glob("*/r_*"))), tolerances.JOIN_WAIT_S
    )
    (run_dir,) = sorted((host.home / "runs").glob("*/r_*"))
    assert wait_until(lambda: _leaves_polling(run_dir), tolerances.JOIN_WAIT_S), (
        "not every leaf created its marker and is polling",
        records.lane_rows(run_dir).rows[-3:],
    )
    return request, run_dir.name, run_dir


def plant_found(run_dir: Path) -> tuple[str, bytes]:
    """A marker the run did not make (a found instance of the waiter's system): a file the run's
    releases must leave exactly as it is."""
    selector = FakeMarker(markers_of(run_dir)).plant_found("waiter", "pre-existing")
    return selector, (markers_of(run_dir) / f"{selector}.marker").read_bytes()


def snapshot_before(host: mcp_host.McpHost, run_id: str, run_dir: Path) -> Stopped:
    created = {s: r for s, r in marker_records(run_dir).items() if r.get("kind") == "process"}
    assert len(created) == len(LEAVES), created
    found_selector, found_bytes = plant_found(run_dir)
    pids = {int(r["pid"]) for r in created.values()}
    before = attributable(host.proc.pid, run_id, pids)
    assert pids <= {p.pid for p in before}, "a marker process is not attributable"
    assert len(before) > len(pids), "the run's own wrapper and child were not seen"
    return Stopped(run_id, run_dir, {}, before, pids, created, found_selector, found_bytes)


def read_stopped(host: mcp_host.McpHost, run: Stopped, request: int, bound_s: float) -> Stopped:
    """The held `run` call's answer: the terminal view, `completion="terminal"`."""
    answer = host.join(request, timeout=bound_s)
    assert isinstance(answer, dict) and "code" not in answer, answer
    assert answer["run_id"] == run.run_id, answer
    run.view = answer
    return run


# ---- what the record and the process table say ------------------------------------------------


def assert_stop_shape(run: Stopped, cause: str, outcome: str) -> None:
    """One stop row of the named cause, the class its cause names, and the offset proof over every
    path with the plugin dead (SA-04): not vacuous, not unproven."""
    answer = run.view["answer"]
    assert run.view["state"] == outcome == run.view["outcome"]["class"], run.view
    assert answer["outcome"] == outcome and answer["root_stop"] == cause, answer
    stops = stop_offset.stop_rows_of(run.run_dir)
    assert [s["cause"] for s in stops] == [cause], stops
    lane = records.lane_rows(run.run_dir)
    verdict = stop_offset.offset_verdict(lane, stops, release_effects(run.run_dir))
    assert verdict.ok and not verdict.vacuous and not verdict.unproven, verdict
    # not vacuous: every leaf applied its create before the offset and gave it back after it
    length = stops[0]["lane_committed_length"]
    creates = [r for r in lane.rows if r.cls == "confirmation" and r.entry["effect"] == "up"]
    assert {r.path for r in creates} == set(LEAVES) and all(r.end <= length for r in creates)
    stops_after = [r for r in lane.rows if r.cls == "confirmation" and r.entry["effect"] == "stop"]
    assert {r.path for r in stops_after} == set(LEAVES)
    # a release may land between the flag and U2's read of the length, so it is not required to lie
    # past the offset; it must follow the create it gives back
    first_stop = {r.path: r.offset for r in stops_after}
    assert all(first_stop[r.path] > r.offset for r in creates)
    # no node started anything after the stop: every leaf's start entries precede the offset
    issues = [r for r in lane.rows if r.cls == "issue" and r.entry["effect"] == "up"]
    assert all(r.offset < length for r in issues), "a create was issued after the stop offset"


def assert_pids_gone(run: Stopped) -> None:
    """Every process attributable before the stop is gone once the terminal row is out, inside the
    published stop bound (MC-13: the survivors of the before snapshot in a fresh one)."""
    bound = clock.stop_bound + tolerances.JOIN_WAIT_S

    def gone() -> bool:
        return not ancestry.survivors(run.before, {p for p in ancestry.snapshot() if _plain(p)})

    assert wait_until(gone, bound), ancestry.survivors(run.before, ancestry.snapshot())


def assert_created_gone_found_untouched(run: Stopped) -> None:
    """The fake's inventory at the terminal answer holds the found marker alone: every marker the
    run created is released, and the found one is byte for byte what was planted."""
    fake = FakeMarker(markers_of(run.run_dir))
    present = fake.inventory()["containers"]
    assert present == frozenset({run.found_selector}), present
    assert set(marker_records(run.run_dir)) == {run.found_selector}
    found_file = markers_of(run.run_dir) / f"{run.found_selector}.marker"
    assert found_file.read_bytes() == run.found_bytes
    released = {r.path for r in records.lane_rows(run.run_dir).rows if r.cls == "released"}
    assert released == set(LEAVES), released


def _cancelled_run(host: mcp_host.McpHost) -> Stopped:
    request, run_id, run_dir = start_and_settle(host, source(short=False))
    run = snapshot_before(host, run_id, run_dir)
    cancelled = host.call("cancel", {"run_id": run_id})
    assert cancelled["code"] == codes.CANCEL_ACCEPTED, cancelled
    return read_stopped(host, run, request, clock.stop_bound + tolerances.JOIN_WAIT_S)


@proves_a63
@proves_cancel
@proves_pids
@proves_created
@proves_bound
def test_root_cancel_readiness_wait_and_running_sibling(mcp: mcp_host.McpHost) -> None:
    """One MCP `cancel` while the waiter polls and the branch's two leaves run: the run is
    `cancelled`, one stop row of cause `cancel`, no applied non-release entry past its offset in
    any path, every process attributable to the run gone, the created markers released and the
    found one untouched; every leaf is `stopped`, none `not started`."""
    run = _cancelled_run(mcp)
    assert_stop_shape(run, "cancel", "cancelled")
    ends = {r.path: r.entry for r in records.lane_rows(run.run_dir).rows if r.cls == "end"}
    assert all(ends[p]["cut"] == "stopped" for p in LEAVES), ends
    assert ends["branch"]["cut"] == "stopped" and ends[""]["cut"] == "stopped"
    assert_pids_gone(run)
    assert_created_gone_found_untouched(run)
    # the run's own processes are dead (the argv marker every wrapper and child carries)
    assert not [p for p in ancestry.snapshot() if run.run_id in p.argv and _plain(p)]


@proves_a63
@proves_deadline
@proves_pids
@proves_created
@proves_bound
def test_root_deadline_same_clauses(mcp: mcp_host.McpHost) -> None:
    """The same tree under a root deadline (the release point ends the leaves' holds, no cancel is
    sent): the run is `timed_out`, one stop row of cause `release_point`, and the same four facts
    hold as for the cancel."""
    request, run_id, run_dir = start_and_settle(mcp, source(short=True))
    run = snapshot_before(mcp, run_id, run_dir)
    read_stopped(mcp, run, request, SHORT_DEADLINE_S + clock.finalization_margin)
    assert_stop_shape(run, "release_point", "timed_out")
    assert_pids_gone(run)
    assert_created_gone_found_untouched(run)
    assert not [p for p in ancestry.snapshot() if run.run_id in p.argv and _plain(p)]
