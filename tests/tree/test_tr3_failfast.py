"""L.TR-3.6: exception fail-fast and the whole-tree stop.

An uncaught exception in any node flips the root's goal to RELEASE (B1-E6): the siblings record no
further non-release entry, are written `cut=STOPPED`, and the answer names the raising node the
primary at origin `WHOLE_ROOT_TRIGGER` (B4-T1). A cancel, or the release point, stops every node
alike: read after the run, no applied non-release entry lies past the stop offset in any path (the
offset proof of SA-04, over a tree). The real runs are a kernel, a wrapper and a child process; the
release-point form is read in-library, where the cause is the only difference (B2-C6)."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import pytest

from tests.core.spine import support
from tests.proof import harness, records, tolerances
from tests.proof.records import LaneRows
from tests.proof.spine import test_stop_offset as suite
from tests.single.workflow import loopkit as kit
from tests.tree import treekit as tk
from trestle.common import clock
from trestle.common.plan import precedence
from trestle.server.main import Kernel
from trestle.workflow.values import StopCause

proves_exception = pytest.mark.proves(
    "WR-UNIT-6", "WR-UNIT-6:exception-siblings-stop", "A", "tree", "PROC+MCP", "BOTH"
)
proves_listed = pytest.mark.proves(
    "WR-UNIT-6", "WR-UNIT-6:exception-in-answer", "A", "tree", "PROC+MCP", "BOTH"
)
proves_cancel = pytest.mark.proves(
    "WR-UNIT-6", "WR-UNIT-6:cancel-no-start-after", "A", "tree", "PROC+MCP", "BOTH"
)
proves_deadline = pytest.mark.proves(
    "WR-UNIT-6", "WR-UNIT-6:deadline-variant", "A", "tree", "PROC+MCP", "BOTH"
)


def _published(kernel: Kernel, tmp_path: Path, name: str) -> Path:
    path = tmp_path / "plugins" / f"{name}.py"
    path.write_text(tk.fixture_source(name), encoding="utf-8")
    return path


def _release_effects(run_dir: Path) -> frozenset[str]:
    """Every effect any node of the run's declared tree marks `is_release`."""
    spec = json.loads((run_dir / "evidence" / "spec.json").read_text(encoding="utf-8"))
    home = run_dir.parents[2]
    declaration = json.loads(
        (home / "snapshots" / spec["snapshot_id"] / "declaration.json").read_text(encoding="utf-8")
    )
    return frozenset(
        e["effect"]
        for node in declaration["nodes"].values()
        for e in node.get("effects", ())
        if e["is_release"]
    )


def _non_release_after(lane: LaneRows, index: int, releases: frozenset[str]) -> list[Any]:
    """Every issue or confirmation after row `index` whose effect is not a release."""
    return [
        (row.path, row.cls, row.entry["effect"])
        for row in lane.rows[index + 1 :]
        if row.cls in ("issue", "confirmation") and row.entry["effect"] not in releases
    ]


@proves_exception
def test_exception_siblings_release_only(tree_kernel: Kernel, tmp_path: Path) -> None:
    path = _published(tree_kernel, tmp_path, "exception_branch")
    admitted = harness.admit_tree(path, {"env": "dev"}, kernel=tree_kernel)
    with support.reaping(admitted.run_id):
        harness.drive_tree(admitted)
    lane = records.lane_rows(admitted.run_dir)
    assert not lane.problems and not lane.torn, lane.problems
    raised = next(
        n
        for n, row in enumerate(lane.rows)
        if row.cls == "step" and row.entry["code"] == "execution.unit_raised"
    )
    assert lane.rows[raised].path == "raiser"
    siblings = [
        r for r in lane.rows if r.cls == "confirmation" and r.path in ("sibling_a", "sibling_b")
    ]
    assert {r.path for r in siblings if r.entry["effect"] == "up"} == {"sibling_a", "sibling_b"}
    # ... and after the exception record no node records a non-release start or confirmation
    assert _non_release_after(lane, raised, _release_effects(admitted.run_dir)) == []
    ends = tk.ends_by_path(admitted.run_dir)
    assert ends["sibling_a"]["cut"] == ends["sibling_b"]["cut"] == "stopped"
    assert (ends["raiser"]["condition"], ends["raiser"]["cut"]) == ("failed", None)


@proves_listed
def test_exception_listed_origin0(tree_kernel: Kernel, tmp_path: Path) -> None:
    path = _published(tree_kernel, tmp_path, "exception_branch")
    admitted = harness.admit_tree(path, {"env": "dev"}, kernel=tree_kernel)
    with support.reaping(admitted.run_id):
        view = harness.drive_tree(admitted)
    answer = view.to_dict()["answer"]
    assert answer["outcome"] == "execution_error"
    primary = answer["primary"]
    assert primary["path"] == ["raiser"] and primary["node_class"] == "execution_error"
    assert primary["code"] == "execution.unit_raised"
    # the whole-root trigger (origin rank 0, B4-T1) and exactly one error record (MC-15)
    assert precedence.origin_of(primary["code"]) is precedence.Origin.WHOLE_ROOT_TRIGGER
    assert (
        answer["error"]["code"] == "execution.unit_raised" and answer["error"]["phase"] == "raiser"
    )
    listed = {tuple(item["path"]): item["listing"] for item in answer["listed"]}
    assert listed[("sibling_a",)] == listed[("sibling_b",)] == "stopped"  # the cut siblings


def _readiness_run(kernel: Kernel, tmp_path: Path) -> tuple[harness.AdmittedTree, Any]:
    """`readiness_sibling` run and cancelled once every leaf has created its marker and is
    polling: returns the admitted run and the answer."""
    path = _published(kernel, tmp_path, "readiness_sibling")
    admitted = harness.admit_tree(path, {"env": "dev"}, kernel=kernel)

    def created() -> bool:
        lane = records.lane_rows(admitted.run_dir)
        made = {
            r.path
            for r in lane.rows
            if r.cls == "confirmation"
            and r.entry["effect"] == "up"
            and r.entry["status"] == "applied"
        }
        return made >= {"waiter", "branch/w1", "branch/w2"}

    views: list[Any] = []
    conductor = threading.Thread(target=lambda: views.append(harness.drive_tree(admitted)))
    with support.reaping(admitted.run_id):
        conductor.start()
        assert support.wait_until(created, tolerances.JOIN_WAIT_S), "not every leaf is polling"
        admitted.kernel.control.cancel(admitted.run_id)
        conductor.join(timeout=clock.stop_bound + tolerances.JOIN_WAIT_S)
        assert not conductor.is_alive(), "the run never reached its terminal row"
    return admitted, views[0].to_dict()["answer"]


@proves_cancel
def test_cancel_no_nonrelease_entry_past_offset_any_node(
    tree_kernel: Kernel, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(clock, "grace", support.TEST_GRACE_S)
    monkeypatch.setattr(clock, "kill", support.TEST_KILL_S)
    admitted, answer = _readiness_run(tree_kernel, tmp_path)
    assert answer["outcome"] == "cancelled" and answer["root_stop"] == "cancel"
    lane = records.lane_rows(admitted.run_dir)
    stops = suite.stop_rows_of(admitted.run_dir)
    releases = _release_effects(admitted.run_dir)
    verdict = suite.offset_verdict(lane, stops, releases)
    assert verdict.ok and not verdict.vacuous and not verdict.unproven, verdict
    # not vacuous: every node applied its create before the offset, and gave it back
    (stop,) = stops
    length = stop["lane_committed_length"]
    creates = [r for r in lane.rows if r.cls == "confirmation" and r.entry["effect"] == "up"]
    assert {r.path for r in creates} == {"waiter", "branch/w1", "branch/w2"}
    assert all(r.end <= length for r in creates)
    stops_after = [r for r in lane.rows if r.cls == "confirmation" and r.entry["effect"] == "stop"]
    assert {r.path for r in stops_after} == {"waiter", "branch/w1", "branch/w2"}
    # the child reads the cancel flag itself and U2 reads the length on its next poll, so a release
    # may land between the two (a loaded runner): it is not required to lie past the offset (the
    # host twin's rule, test_trl_cancel); it must follow the create it gives back
    first_stop = {r.path: r.offset for r in stops_after}
    assert all(first_stop[r.path] > r.offset for r in creates)
    assert not support.marked(admitted.run_id)  # the plugin is dead: nothing of the run is alive


def _held_tree(tmp_path: Path, gate: threading.Event, holding: threading.Barrier) -> tk.TreeRig:
    """`readiness_sibling`'s shape (a waiter and a branch of two) with leaves that create and
    then hold in their wait, so a stop is raised while every node is mid-wait."""
    arrived: set[str] = set()

    def arrive(path: str) -> None:
        arrived.add(path)
        holding.wait()

    branch = tk.group("branch", (tk.bind("w1"), tk.bind("w2")), concurrency=2, budget_s=100)
    app = tk.group("app", (tk.bind("waiter"), tk.bind("branch")), concurrency=3, budget_s=150)
    units: dict[str, object] = {
        "branch": branch,
        **{n: tk.held_unit(n, gate, on_hold=arrive) for n in ("waiter", "w1", "w2")},
    }
    return tk.tree_rig(tmp_path, app, units)


@proves_deadline
@pytest.mark.parametrize("cause", [StopCause.CANCEL, StopCause.RELEASE_POINT])
def test_release_point_same_as_cancel(tmp_path: Path, cause: StopCause) -> None:
    """The release point stops the tree exactly as a cancel does (B2-C6: one flag protocol): the
    same ends and the same offset proof, whichever cause raised the flag."""
    gate = threading.Event()
    holding = threading.Barrier(4, timeout=tolerances.JOIN_WAIT_S)
    rig = _held_tree(tmp_path / cause.value, gate, holding)
    runner = threading.Thread(target=rig.run)
    runner.start()
    try:
        holding.wait()  # every leaf created and is inside its wait
        rig.rig.cancel.stop = cause
        offset = rig.rig.services.attempts().committed_length()
    finally:
        gate.set()
        runner.join(tolerances.JOIN_WAIT_S)
    assert not runner.is_alive()
    lane = records.lane_rows(rig.run_dir)
    stop = {"cause": cause.value, "lane_committed_length": offset}
    verdict = suite.offset_verdict(lane, [stop], frozenset({kit.STOP_EFFECT}))
    assert verdict.ok and not verdict.vacuous, verdict
    ends = rig.ends()
    leaves = ("waiter", "branch/w1", "branch/w2")
    assert all(ends[p]["cut"] == "stopped" for p in leaves)
    assert ends["branch"]["cut"] == "stopped" and ends[""]["cut"] == "stopped"
    released = {r.path for r in lane.rows if r.cls == "released"}
    assert released == set(leaves)  # each gave back what it made, after every end
