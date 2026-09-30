"""L.TR-L.5: the WR-UNIT-3 slice and root-deadline claims, through the host (A5.4).

`tests/tree/test_tr3_slices.py` proves the slice rules in-library over a manual clock. Here the
same two fixtures run as real runs: `ControlSurface.run` admits the published tree (TM-B2-1's
AllDeclaration refusal is lifted by L.TR-L.1), a conductor drives it, a wrapper and a child
process execute it, and the tree walk (`run_tree`) runs on the child's threads. Nothing here
reaches `tests/proof/harness.py` (MC-B3-02): the in-process host is the kernel's own control
surface, published and called like any caller would.

- `slice_noncoop_grandchild`: a leaf that never consults the cancel signal starts a process tree
  (a process that ignores SIGTERM, and its own child, both carrying a tag in argv, MC-13). The root
  deadline stops the run; no process attributable to it is alive once the run answers, which is
  bounded by the deadline plus the finalization margin plus the kill bound (MC-09).
- `slice_coop`: a leaf that cooperates is ended at its own carved slice, `CARVE_EXCEEDED` naming
  its path; the answer is `timed_out`, and from that verdict the node issues only release starts.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import pytest

from tests.fixtures.trees import slice_coop, slice_noncoop_grandchild
from tests.proof import ancestry, records, tolerances
from tests.tree import hostpath
from trestle.common import clock, codes
from trestle.common.types import PublishView, RunView
from trestle.server.main import Kernel

REPO = Path(__file__).resolve().parents[3]
TREES = REPO / "tests" / "fixtures" / "trees"

TERMINAL_STATES = ("succeeded", "failed", "cancelled", "timed_out")
# A marker names one run (its run id or its tag); anything shorter matches unrelated processes.
MIN_MARKER = 8
# A stop's own bound in a test that shrinks `grace` and `kill` so the suite stays fast.
TEST_GRACE_S = tolerances.SETTLE_LONG_S
TEST_KILL_S = tolerances.SETTLE_LONG_S * 2

proves_a54 = pytest.mark.proves("A5.4", "A5.4", "A", "tree", "PROC", "BOTH")
proves_dead = pytest.mark.proves(
    "WR-UNIT-3", "WR-UNIT-3:descendant-dead-by-root-bound", "A", "tree", "LOGIC+PROC", "BOTH"
)
proves_coop = pytest.mark.proves(
    "WR-UNIT-3", "WR-UNIT-3:coop-child-stopped-at-slice", "A", "tree", "LOGIC+PROC", "BOTH"
)
proves_nostart = pytest.mark.proves(
    "WR-UNIT-3", "WR-UNIT-3:no-start-after-slice-verdict", "A", "tree", "LOGIC+PROC", "BOTH"
)


def _publish(kernel: Kernel, name: str) -> str:
    text = (TREES / f"{name}.py").read_text(encoding="utf-8")
    view = hostpath.publish_tree_via_host(kernel, text)
    assert isinstance(view, PublishView), view
    return view.name


def _run_dirs(kernel: Kernel) -> list[Path]:
    runs = kernel.home / "runs"
    return sorted(p for p in runs.glob("*/*") if p.is_dir()) if runs.exists() else []


def _marked(marker: str) -> set[ancestry.ProcInfo]:
    """Every process in a fresh snapshot whose argv carries `marker`."""
    assert len(marker) >= MIN_MARKER, f"marker {marker!r} is too short to name one run"
    return {p for p in ancestry.snapshot() if marker in p.argv}


def _wait_until(predicate: Any, bound_s: float) -> bool:
    end = time.monotonic() + bound_s
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(tolerances.POLL_S)
    return bool(predicate())


def _ancestors(proc: ancestry.ProcInfo, table: dict[int, ancestry.ProcInfo]) -> set[int]:
    chain: set[int] = set()
    current = proc
    while current.ppid in table and current.ppid not in chain and current.ppid > 1:
        chain.add(current.ppid)
        current = table[current.ppid]
    return chain


def _bound_s(deadline_s: float) -> float:
    """The longest a run of this deadline may take to answer (MC-09): the deadline, the finalization
    margin after it, and the kill bound for what ignores the stop."""
    return deadline_s + clock.finalization_margin + clock.kill


def _lane(run_dir: Path) -> list[dict[str, Any]]:
    lane = records.lane_rows(run_dir)
    assert not lane.problems and not lane.torn, lane.problems
    return [row.entry for row in lane.rows]


def _index(rows: list[dict[str, Any]], **fields: Any) -> int:
    return next(n for n, row in enumerate(rows) if all(row.get(k) == v for k, v in fields.items()))


@proves_a54
@proves_dead
def test_noncooperating_child_dead_by_root_deadline_margin_kill(
    tree_kernel: Kernel, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`slice_noncoop_grandchild` through `ControlSurface.run`: its leaf starts a process that
    ignores SIGTERM and its own child (both attributable to the run by ancestry and by the tag in
    argv, MC-13) and then never listens to the cancel signal. The run answers `timed_out` within
    the root deadline plus the finalization margin plus the kill bound, and no process attributable
    to the run is alive at that answer."""
    monkeypatch.setattr(clock, "grace", TEST_GRACE_S)
    monkeypatch.setattr(clock, "kill", TEST_KILL_S)
    deadline_s = float(slice_noncoop_grandchild.DEADLINE_S)
    bound_s = _bound_s(deadline_s)
    name = _publish(tree_kernel, "slice_noncoop_grandchild")
    tag = str(tmp_path / "noncoop-tree")
    assert len(tag) >= MIN_MARKER

    outcome: dict[str, Any] = {}

    def call() -> None:
        started = time.monotonic()
        # the leaf sleeps past the whole bound: only the runtime's stop takes its process tree away
        outcome["view"] = tree_kernel.control.run(
            plugin=name,
            args={"tag": tag, "seconds": bound_s + tolerances.JOIN_WAIT_S},
            wait_ms=int((bound_s + tolerances.JOIN_WAIT_S) * 1000),
            completion="terminal",
        )
        outcome["elapsed"] = time.monotonic() - started

    caller = threading.Thread(target=call, daemon=True)
    seen: set[ancestry.ProcInfo] = set()
    run_id = ""
    try:
        caller.start()
        assert _wait_until(lambda: len(_marked(tag)) >= 2, tolerances.JOIN_WAIT_S), (
            "the leaf's process tree never appeared"
        )
        dirs = _run_dirs(tree_kernel)
        assert len(dirs) == 1, dirs
        run_id = dirs[0].name
        table = {p.pid: p for p in ancestry.snapshot()}
        run_processes = {p.pid for p in _marked(run_id)}
        assert run_processes, "no process carries the run's id"
        seen = _marked(tag)
        assert len(seen) == 2, seen  # the process the leaf started and its own child
        for proc in seen:
            assert _ancestors(proc, table) & run_processes, proc  # attributable to the run (MC-13)
        caller.join(bound_s + tolerances.JOIN_WAIT_S * 2)
        assert not caller.is_alive(), "the run never answered"
    finally:
        ancestry.reap(_marked(tag))  # a failed test must not leave the sleepers behind

    view = outcome["view"]
    assert isinstance(view, RunView), view
    assert view.state in TERMINAL_STATES, view.state
    assert outcome["elapsed"] <= bound_s, (outcome["elapsed"], bound_s)  # deadline + margin + kill
    assert outcome["elapsed"] >= deadline_s - clock.release_slice, (
        "the run ended before its deadline"
    )
    assert view.answer is not None
    assert view.answer["outcome"] == "timed_out", view.answer
    assert view.answer["incomplete"] == ["stubborn"], view.answer
    assert view.answer["cleanup"]["group_confirmed_gone"] is True, view.answer["cleanup"]

    # no attributable descendant alive at the answer: nothing carries the tag or the run's id
    assert ancestry.survivors(seen, _marked(tag)) == set()
    assert _marked(tag) == set()
    assert _wait_until(lambda: not _marked(run_id), tolerances.JOIN_WAIT_S)


@proves_a54
@proves_coop
@proves_nostart
def test_cooperating_child_timed_out_names_path(tree_kernel: Kernel) -> None:
    """`slice_coop` through `ControlSurface.run`: `slow` polls until its carved slice ends. Its
    `NodeEnd` is `FAILED` `CARVE_EXCEEDED` at its own path, the answer is `timed_out` naming that
    path, the sibling is untouched, and after the verdict `slow` issues only release starts."""
    name = _publish(tree_kernel, "slice_coop")
    deadline_s = float(slice_coop.DEADLINE_S)
    view = tree_kernel.control.run(
        plugin=name,
        args={"env": "dev"},
        wait_ms=int(_bound_s(deadline_s) * 1000),
        completion="terminal",
    )
    assert isinstance(view, RunView), view
    assert view.state in TERMINAL_STATES, view.state

    answer = view.answer
    assert answer is not None
    assert answer["outcome"] == "timed_out", answer
    primary = answer["primary"]
    assert primary["path"] == ["slow"], primary  # the verdict names the path
    assert primary["node_class"] == "timed_out" and primary["condition"] == "failed", primary
    assert primary["code"] == codes.CARVE_EXCEEDED, primary
    assert answer["incomplete"] == ["slow"], (
        answer
    )  # nothing STOPPED or UNENDED: the primary's path

    dirs = _run_dirs(tree_kernel)
    assert [d.name for d in dirs] == [view.run_id]
    rows = _lane(dirs[0])
    ends = {row["path"]: row for row in rows if row["class"] == "end"}
    assert (ends["slow"]["condition"], ends["slow"]["code"], ends["slow"]["cut"]) == (
        "failed",
        codes.CARVE_EXCEEDED,
        None,
    )
    assert ends["quick"]["condition"] == "satisfied"  # a sibling is not cut by it

    step = _index(rows, **{"class": "step", "path": "slow", "code": codes.CARVE_EXCEEDED})
    verdict = _index(rows, **{"class": "end", "path": "slow"})
    assert step < verdict
    after = [row for row in rows[verdict:] if row.get("path") == "slow" and row["class"] == "issue"]
    assert after, "the timed-out node's resources were never released"
    assert {row["effect"] for row in after} == {"stop"}, (
        after
    )  # only release starts after the verdict
