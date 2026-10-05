"""L.TR-5.6: root cancel and root deadline of a ChoiceNode tree through the host.

`choice_long_running` (MC-B3-01) is a root over one choice between `steady` and `spare`. Nothing of
either is up, so the selection takes the declared fallback `steady`; it creates its marker (a fake
in-run process) and polls for a readiness that never comes: a long step. A run of it goes through
the MCP `run` tool of a real `trestle serve` (MC-12; `tests/tree/hostpath.py`), never MC-26, and is
read after its terminal row with the plugin dead, exactly as L.TR-L.8 reads an `AllDeclaration`
tree (R-7, DM-58):

* cancel: one MCP `cancel` while the selected alternative is mid-step;
* deadline: the same tree under a short root deadline, whose leaf waits cooperatively past the
  release point, so the deadline (not a slice or a wait) is what ends the run.

Each run shows the four things the clause names: no applied non-release lane entry past the stop
offset in any path (SA-04, with the plugin dead), every attributable pid gone within the published
stop bound (MC-13), the resource the run created released and one it only found left as it was, and
a terminal answer of the class the cause names; plus what a choice adds: the selection was
recorded before the first effect and the alternative it left out was never walked. Every timing
bound is `tests/proof/tolerances.py` or `trestle.common.clock` (SA-05); the deadline variant edits
the fixture's own constants, which are the tree's declared budgets, not test timings.

The suite `tests/tree/test_tr5_termination.py` enumerates `choice_long_running` at run time (A2c2-3)
and is run before this module first admits it (the leaf's verifier)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from trestle_packs.fakes import FakeMarker

from tests.proof import ancestry, mcp_host, records, tolerances
from tests.proof.spine import test_stop_offset as stop_offset
from tests.tree import hostpath
from tests.tree.host import test_trl_cancel as l8
from trestle.common import clock, codes

TREES = Path(__file__).resolve().parents[2] / "fixtures" / "trees"
FIXTURE = "choice_long_running"
LEAF = "pick/steady"  # the alternative the selection takes (the declared fallback)
LEFT_OUT = "pick/spare"
HOST_TIMEOUT_S = tolerances.JOIN_WAIT_S * 6

proves_stop = pytest.mark.proves(
    "C-CONTAIN-AND-CLOCK", "C-CONTAIN-AND-CLOCK:choice-tree-stop", "A", "tree", "PROC+MCP", "BOTH"
)

# The deadline variant's source edits (as L.TR-L.8's, over this fixture's own constants): the
# budgets nest by the reserve (leaf + reserve = choice, + reserve = root) and the deadline is the
# root budget plus the release slice plus a margin, so shortening it shortens every constant
# together; the leaf's third observation (the selection's, its own first look, then its first
# poll after its create) then holds
# cooperatively until the stop is raised, so only the root deadline (release point) ends the run.
SHORT_DEADLINE_S = 38
SHORT = {
    "LEAF_BUDGET_S = 34": "LEAF_BUDGET_S = 6",
    "CHOICE_BUDGET_S = 44": "CHOICE_BUDGET_S = 16",
    "ROOT_BUDGET_S = 54": "ROOT_BUDGET_S = 26",
    "DEADLINE_S = 70": f"DEADLINE_S = {SHORT_DEADLINE_S}",
    "@trestle(deadline=70,": f"@trestle(deadline={SHORT_DEADLINE_S},",
    "timedelta(seconds=30)),": "timedelta(seconds=2)),",
    "        self._unit = unit\n": "        self._unit = unit\n        self._seen = 0\n",
    "        resource = reads.read(ResourceReads)\n": (
        "        self._seen += 1\n"
        "        if self._seen == 3:  # selection, first look, first poll after the create: hold\n"
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


def _leaf_polling(run_dir: Path) -> bool:
    made = {
        r.path
        for r in records.lane_rows(run_dir).rows
        if r.cls == "confirmation" and r.entry["effect"] == "up" and r.entry["status"] == "applied"
    }
    return LEAF in made and len(l8.process_markers(run_dir)) == 1


def start_and_settle(host: mcp_host.McpHost, text: str) -> tuple[int, str, Path]:
    """Publish the fixture into the host and start a run through the MCP `run` tool as
    `hostpath.mcp_run_tree` does (`completion="terminal"`), held so a `cancel` can flow on the same
    connection; then wait until the selected alternative has created its marker and is polling.
    Returns the held request id, the run id and the run directory."""
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
    assert l8.wait_until(
        lambda: bool(list((host.home / "runs").glob("*/r_*"))), tolerances.JOIN_WAIT_S
    )
    (run_dir,) = sorted((host.home / "runs").glob("*/r_*"))
    assert l8.wait_until(lambda: _leaf_polling(run_dir), tolerances.JOIN_WAIT_S), (
        "the selected alternative did not create its marker and poll",
        records.lane_rows(run_dir).rows[-3:],
    )
    return request, run_dir.name, run_dir


def snapshot_before(host: mcp_host.McpHost, run_id: str, run_dir: Path) -> l8.Stopped:
    created = {s: r for s, r in l8.marker_records(run_dir).items() if r.get("kind") == "process"}
    assert len(created) == 1, created
    # a marker the run did not make (a found instance of the selected alternative's system): the
    # run's releases must leave it exactly as it is
    found = FakeMarker(l8.markers_of(run_dir)).plant_found("steady", "pre-existing")
    found_bytes = (l8.markers_of(run_dir) / f"{found}.marker").read_bytes()
    pids = {int(r["pid"]) for r in created.values()}
    before = l8.attributable(host.proc.pid, run_id, pids)
    assert pids <= {p.pid for p in before}, "a marker process is not attributable"
    assert len(before) > len(pids), "the run's own wrapper and child were not seen"
    return l8.Stopped(run_id, run_dir, {}, before, pids, created, found, found_bytes)


def assert_choice_recorded(run: l8.Stopped) -> None:
    """What a choice adds: the selection (the declared fallback) is in the plan entry, which is
    the lane's first entry; the alternative it left out has no `NodeEnd` and no ticket."""
    lane = records.lane_rows(run.run_dir)
    assert lane.rows[0].cls == "plan", [r.cls for r in lane.rows]
    assert lane.rows[0].entry["selection"] == {"pick": LEAF}, lane.rows[0].entry
    assert not [r for r in lane.rows if r.path is not None and r.path.startswith(LEFT_OUT)]


def assert_stop_shape(run: l8.Stopped, cause: str, outcome: str) -> None:
    """One stop row of the named cause, the class its cause names, and the offset proof over every
    path with the plugin dead (SA-04): not vacuous, not unproven."""
    answer = run.view["answer"]
    assert run.view["state"] == outcome == run.view["outcome"]["class"], run.view
    assert answer["outcome"] == outcome and answer["root_stop"] == cause, answer
    stops = stop_offset.stop_rows_of(run.run_dir)
    assert [s["cause"] for s in stops] == [cause], stops
    lane = records.lane_rows(run.run_dir)
    verdict = stop_offset.offset_verdict(lane, stops, l8.release_effects(run.run_dir))
    assert verdict.ok and not verdict.vacuous and not verdict.unproven, verdict
    # not vacuous: the leaf applied its create before the offset and gave it back after its own
    length = stops[0]["lane_committed_length"]
    creates = [r for r in lane.rows if r.cls == "confirmation" and r.entry["effect"] == "up"]
    assert {r.path for r in creates} == {LEAF} and all(r.end <= length for r in creates)
    gives = [r for r in lane.rows if r.cls == "confirmation" and r.entry["effect"] == "stop"]
    assert {r.path for r in gives} == {LEAF} and gives[0].offset > creates[0].offset
    # nothing started after the stop: every create was issued before the offset
    issues = [r for r in lane.rows if r.cls == "issue" and r.entry["effect"] == "up"]
    assert all(r.offset < length for r in issues), "a create was issued after the stop offset"
    ends = {r.path: r.entry for r in lane.rows if r.cls == "end"}
    assert ends[LEAF]["cut"] == "stopped", ends  # the selected alternative was cut mid-step
    if cause == "cancel":  # a deadline run's choice composite may end at its own slice first
        assert ends["pick"]["cut"] == "stopped" and ends[""]["cut"] == "stopped", ends


def assert_pids_gone(run: l8.Stopped) -> None:
    """Every process attributable before the stop is gone once the terminal row is out, inside the
    published stop bound (MC-13: the survivors of the before snapshot in a fresh one)."""
    bound = clock.stop_bound + tolerances.JOIN_WAIT_S

    def gone() -> bool:
        table = {p for p in ancestry.snapshot() if l8._plain(p)}  # noqa: SLF001
        return not ancestry.survivors(run.before, table)

    assert l8.wait_until(gone, bound), ancestry.survivors(run.before, ancestry.snapshot())
    assert not [
        p
        for p in ancestry.snapshot()
        if run.run_id in p.argv and l8._plain(p)  # noqa: SLF001
    ]


def assert_created_gone_found_untouched(run: l8.Stopped) -> None:
    """The fake's inventory at the terminal answer holds the found marker alone: the marker the run
    created is released, and the found one is byte for byte what was planted."""
    present = FakeMarker(l8.markers_of(run.run_dir)).inventory()["containers"]
    assert present == frozenset({run.found_selector}), present
    assert set(l8.marker_records(run.run_dir)) == {run.found_selector}
    found_file = l8.markers_of(run.run_dir) / f"{run.found_selector}.marker"
    assert found_file.read_bytes() == run.found_bytes
    released = {r.path for r in records.lane_rows(run.run_dir).rows if r.cls == "released"}
    assert released == {LEAF}, released


def read_stopped(host: mcp_host.McpHost, run: l8.Stopped, request: int, bound_s: float) -> None:
    """The held `run` call's answer: the terminal view, `completion="terminal"`."""
    answer = host.join(request, timeout=bound_s)
    assert isinstance(answer, dict) and "code" not in answer, answer
    assert answer["run_id"] == run.run_id, answer
    run.view = answer


@proves_stop
def test_choice_tree_root_cancel_contained(mcp: mcp_host.McpHost) -> None:
    """One MCP `cancel` while the selected alternative is mid-step: the run is `cancelled`, one stop
    row of cause `cancel`, no applied non-release entry past its offset, every process attributable
    to the run gone, the created marker released and the found one untouched; the selection was
    recorded first and the alternative it left out was never walked."""
    request, run_id, run_dir = start_and_settle(mcp, source(short=False))
    run = snapshot_before(mcp, run_id, run_dir)
    cancelled = mcp.call("cancel", {"run_id": run_id})
    assert cancelled["code"] == codes.CANCEL_ACCEPTED, cancelled
    read_stopped(mcp, run, request, clock.stop_bound + tolerances.JOIN_WAIT_S)
    assert_stop_shape(run, "cancel", "cancelled")
    assert_choice_recorded(run)
    assert_pids_gone(run)
    assert_created_gone_found_untouched(run)


@proves_stop
def test_choice_tree_root_deadline_contained(mcp: mcp_host.McpHost) -> None:
    """The same tree under a root deadline (the release point ends the leaf's hold, no cancel is
    sent): the run is `timed_out`, one stop row of cause `release_point`, and the same facts hold
    (the created marker released after the deadline plus margin, no attributable pid alive)."""
    request, run_id, run_dir = start_and_settle(mcp, source(short=True))
    run = snapshot_before(mcp, run_id, run_dir)
    read_stopped(mcp, run, request, SHORT_DEADLINE_S + clock.finalization_margin)
    assert_stop_shape(run, "release_point", "timed_out")
    assert_choice_recorded(run)
    assert_pids_gone(run)
    assert_created_gone_found_untouched(run)
