"""L.SV-3.6: the stop protocol of B2-C10 and B2-C15. The request path writes only the cancel flag;
the conductor (U2) records one stop row per stop (`stop_row`: cause, lane committed length), writes
the release-point flag itself, waits the root's release slice and then kills; the class is the
first stop row's cause; a run stopped while queued gets a stop row at length 0 and no process.

Every timing bound comes from `tests.proof.tolerances` and `trestle.common.clock` (SA-05); no
timing literal appears here. Reaping markers are run ids (>= 8 chars, `support.marked`).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from tests.core.spine import support
from tests.proof import harness, tolerances
from trestle.common import clock
from trestle.common import lane_format as lf
from trestle.common.fsutil import atomic_write_json
from trestle.common.plan import carving
from trestle.common.plan.compiler import AdmittedPlan
from trestle.common.types import RunView
from trestle.server import fold
from trestle.server.runs import RunRegistry, cancel_flag_path, release_point_flag_path

REPO = Path(__file__).resolve().parents[3]
LONG_S = tolerances.JOIN_WAIT_S * 6
# a release slice for a test that measures it: a couple of settle units
SLICE_S = tolerances.SETTLE_LONG_S * 2


@pytest.fixture(autouse=True)
def short_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(clock, "grace", support.TEST_GRACE_S)
    monkeypatch.setattr(clock, "kill", support.TEST_KILL_S)


def _kinds(run_dir: Path) -> list[str]:
    """The ledger's row kinds (not `support.kinds`, which also reads the lane)."""
    return [str(row["kind"]) for row in support.rows(run_dir)]


def _stop_rows(run_dir: Path) -> list[dict[str, Any]]:
    return support.rows_of(run_dir, "stop_row")


def _set_release_slice(run_dir: Path, slice_s: float) -> None:
    """Re-carve the admitted plan of `run_dir` with this release slice (digest recomputed)."""
    spec_path = run_dir / "evidence" / "spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    plan = AdmittedPlan.from_json(json.dumps(spec["plan"]))
    spec["plan"] = json.loads(carving.attach(plan, {}, slice_s).to_json())
    atomic_write_json(spec_path, spec)


def _terminal(run_dir: Path, thread: threading.Thread, extra_s: float = 0.0) -> str:
    thread.join(
        timeout=support.SHORT_DEADLINE_S + tolerances.JOIN_WAIT_S + clock.stop_bound + extra_s
    )
    assert not thread.is_alive(), "conductor never returned"
    return _kinds(run_dir)[-1]


class _Spy:
    """Records every os.kill / os.killpg made in this process, with the calling thread."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: list[tuple[str, int, str]] = []
        real_kill, real_killpg = os.kill, os.killpg

        def kill(pid: int, signum: int) -> None:
            self.calls.append(("kill", int(signum), threading.current_thread().name))
            real_kill(pid, signum)

        def killpg(pgid: int, signum: int) -> None:
            self.calls.append(("killpg", int(signum), threading.current_thread().name))
            real_killpg(pgid, signum)

        monkeypatch.setattr(os, "kill", kill)
        monkeypatch.setattr(os, "killpg", killpg)


@pytest.mark.proves("WR-CANCEL-1", "WR-CANCEL-1:u2-sole-signaller", "A", "single", "PROC", "BOTH")
def test_request_path_writes_only_flag(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A cancel request signals nothing: not through the registry it always used, not through the
    control surface while a run is live; the conductor's thread is the only one that does."""
    # (1) the registry, with a live registered process it could have stopped before
    run_dir = tmp_path / "run"
    (run_dir / "work").mkdir(parents=True)
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)", str(tmp_path / "hold-marker")],
        start_new_session=True,
    )
    spy = _Spy(monkeypatch)
    try:
        registry = RunRegistry()
        registry.register("r_flag_only", proc, None)  # type: ignore[arg-type]
        registry.request_cancel("r_flag_only", run_dir)
        assert cancel_flag_path(run_dir).exists()
        assert proc.poll() is None and spy.calls == []
    finally:
        proc.kill()
        proc.wait(timeout=tolerances.PROC_WAIT_S)

    # (2) the control surface over a live run: every signal comes from the conductor's thread
    kernel = support.spine_kernel()
    order = support.admit_order(kernel, "tree", {"seconds": LONG_S})
    run_dir = support.run_dir_of(kernel, order.run_id)
    thread = support.drive_in_thread(kernel, order)
    with support.reaping(order.run_id):
        support.wait_ready(run_dir)
        spy.calls.clear()
        kernel.control.cancel(order.run_id)
        assert threading.current_thread().name not in {name for _, _, name in spy.calls}
        assert _terminal(run_dir, thread) == "cancelled"
    assert spy.calls, "the conductor never stopped the run"
    assert all(name == thread.name for _, _, name in spy.calls), spy.calls


def test_u2_stop_row_length_and_cause_cancel() -> None:
    kernel = support.spine_kernel()
    order = support.admit_order(kernel, "tree", {"seconds": LONG_S})
    run_dir = support.run_dir_of(kernel, order.run_id)
    thread = support.drive_in_thread(kernel, order)
    with support.reaping(order.run_id):
        support.wait_ready(run_dir)
        # a committed lane prefix the stop row must measure (the plan entry, one line)
        plan = lf.PlanEntry(
            lf.PlanIdentity("d" * 64, "a" * 64, {}, "o" * 64),
            seq=1,
        )
        data = lf.encode_entry(plan, 1)
        lane = lf.lane_path(run_dir)
        lane.write_bytes(data)
        kernel.control.cancel(order.run_id)
        assert _terminal(run_dir, thread) == "cancelled"
        (row,) = _stop_rows(run_dir)
        assert row["cause"] == fold.CAUSE_CANCEL
        assert row["lane_committed_length"] == len(data)
        assert cancel_flag_path(run_dir).exists()
        kinds = _kinds(run_dir)
        assert kinds.index("stop_row") < kinds.index("group_stop")  # the stop row comes first


@pytest.mark.proves(
    "WR-DEADLINE-2", "WR-DEADLINE-2:release-point-stop-row", "A", "single", "PROC", "BOTH"
)
def test_release_point_flag_then_stop_row() -> None:
    kernel = support.spine_kernel()
    with harness.patch_snapshot(kernel, "tree", timeout_s=support.SHORT_DEADLINE_S):
        order = support.admit_order(kernel, "tree", {"seconds": LONG_S})
    run_dir = support.run_dir_of(kernel, order.run_id)
    _set_release_slice(run_dir, SLICE_S)
    thread = support.drive_in_thread(kernel, order)
    with support.reaping(order.run_id):
        # the release point is the deadline less the slice: U2 writes its flag then, and only then
        assert support.wait_until(
            release_point_flag_path(run_dir).exists, support.SHORT_DEADLINE_S + SLICE_S
        )
        assert _terminal(run_dir, thread) == "timed_out"
        (row,) = _stop_rows(run_dir)
        assert row["cause"] == fold.CAUSE_RELEASE_POINT
        assert not cancel_flag_path(
            run_dir
        ).exists()  # no cancel: the class is the cause, not a file


def test_class_from_first_stop_row_when_both_flags() -> None:
    # (1) both hold before the run is spawned: the cancel is the first (and only) stop row
    kernel = support.spine_kernel()
    with harness.patch_snapshot(kernel, "slow", timeout_s=support.SHORT_DEADLINE_S):
        order = support.admit_order(kernel, "slow", {"seconds": LONG_S})
    time.sleep(support.SHORT_DEADLINE_S + tolerances.SETTLE_SHORT_S)
    run_dir = support.run_dir_of(kernel, order.run_id)
    cancel_flag_path(run_dir).parent.mkdir(parents=True, exist_ok=True)
    cancel_flag_path(run_dir).write_bytes(b"1")
    thread = support.drive_in_thread(kernel, order)
    assert _terminal(run_dir, thread) == "cancelled"
    assert [r["cause"] for r in _stop_rows(run_dir)] == [fold.CAUSE_CANCEL]

    # (2) the release point is first; a cancel arrives while the stop waits out the release slice:
    # the class stays timed_out and there is still exactly one stop row
    kernel = support.spine_kernel()
    with harness.patch_snapshot(kernel, "tree", timeout_s=support.SHORT_DEADLINE_S):
        order = support.admit_order(kernel, "tree", {"seconds": LONG_S})
    run_dir = support.run_dir_of(kernel, order.run_id)
    _set_release_slice(run_dir, SLICE_S)
    thread = support.drive_in_thread(kernel, order)
    with support.reaping(order.run_id):
        assert support.wait_until(
            release_point_flag_path(run_dir).exists, support.SHORT_DEADLINE_S + SLICE_S
        )
        kernel.control.cancel(order.run_id)
        assert _terminal(run_dir, thread) == "timed_out"
        assert [r["cause"] for r in _stop_rows(run_dir)] == [fold.CAUSE_RELEASE_POINT]


def test_exit_after_release_point_records_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    """A root that exits between two looks of the supervisor, after the release point passed,
    still gets its stop row when the exit is observed (B2-C10): the class is `timed_out` and no
    flag is written, since no process is left to read one."""
    monkeypatch.setattr(clock, "poll_interval", support.SHORT_DEADLINE_S * 2)
    kernel = support.spine_kernel()
    with harness.patch_snapshot(kernel, "slow", timeout_s=support.SHORT_DEADLINE_S):
        order = support.admit_order(kernel, "slow", {"seconds": support.SHORT_DEADLINE_S + 1})
    run_dir = support.run_dir_of(kernel, order.run_id)
    thread = support.drive_in_thread(kernel, order)
    with support.reaping(order.run_id):
        assert _terminal(run_dir, thread, extra_s=support.SHORT_DEADLINE_S * 2) == "timed_out"
        (row,) = _stop_rows(run_dir)
        assert row["cause"] == fold.CAUSE_RELEASE_POINT
        assert not release_point_flag_path(run_dir).exists()
        assert not cancel_flag_path(run_dir).exists()


def test_unreadable_lane_stop_row_length_none() -> None:
    """The row is still appended when the committed length cannot be read: `None`, never a
    passed ordering proof (B2-C15 'Fails')."""
    kernel = support.spine_kernel()
    order = support.admit_order(kernel, "tree", {"seconds": LONG_S})
    run_dir = support.run_dir_of(kernel, order.run_id)
    lf.lane_path(run_dir).mkdir(parents=True)  # a lane path that cannot be read as a file
    thread = support.drive_in_thread(kernel, order)
    with support.reaping(order.run_id):
        support.wait_ready(run_dir)
        kernel.control.cancel(order.run_id)
        assert _terminal(run_dir, thread) == "cancelled"
        (row,) = _stop_rows(run_dir)
        assert row["cause"] == fold.CAUSE_CANCEL and row["lane_committed_length"] is None


def _state(kernel: Any, run_id: str) -> str:
    view = kernel.control.project.status(run_id)
    assert isinstance(view, RunView), view
    return view.state


def _submit(kernel: Any, seconds: float) -> str:
    """Submit a `slow` run without waiting; its run id."""
    view = kernel.control.run(plugin="slow", args={"seconds": seconds}, wait_ms=0)
    assert isinstance(view, RunView), view
    return view.run_id


def _queue_behind_a_running_run(kernel: Any, holder_seconds: float) -> tuple[str, str]:
    """Fill the only run slot with a live run, then admit a second: it waits in the queue.
    Returns (holder run id, queued run id)."""
    # the pool size is read from the home's config.toml at each admission (v0.4 rule 11)
    (kernel.home / "config.toml").write_text("max_running_runs = 1\n", encoding="utf-8")
    holder = _submit(kernel, holder_seconds)
    queued = _submit(kernel, holder_seconds)
    assert support.wait_until(lambda: _state(kernel, holder) == "running", tolerances.JOIN_WAIT_S)
    assert _state(kernel, queued) == "queued"
    return holder, queued


def _assert_finalized_unspawned(kernel: Any, run_id: str, terminal: str, cause: str) -> None:
    run_dir = support.run_dir_of(kernel, run_id)
    kinds = _kinds(run_dir)
    assert kinds[-1] == terminal
    (row,) = _stop_rows(run_dir)
    assert row["cause"] == cause and row["lane_committed_length"] == 0
    # no process, no lane, no kill, fold or sweep: nothing of a spawned run's rows exists
    for kind in ("started", "process_identity", "group_stop", "lane_folded"):
        assert kind not in kinds, kind
    assert not lf.lane_path(run_dir).exists()
    # what B4 is handed (B2-C12): an empty fold with the one stop row, and nothing created
    folded = fold.fold_lane(run_dir, None)
    assert [(r.cause, r.lane_committed_length) for r in folded.stop_rows] == [(cause, 0)]
    assert not (folded.entries or folded.steps or folded.ends or folded.unknown_paths)
    assert not (folded.ended or folded.overflowed or folded.refused_full)
    assert folded.plan is None
    view = kernel.control.project.status(run_id)
    assert isinstance(view, RunView)
    assert view.cleanup is not None and view.cleanup.processes == "nothing_created"
    assert not support.marked(run_id)


def test_queued_cancel_stop_row_length_zero_cancelled() -> None:
    kernel = support.spine_kernel()
    holder, queued = _queue_behind_a_running_run(kernel, LONG_S)
    with support.reaping(holder):
        kernel.control.cancel(queued)
        _assert_finalized_unspawned(kernel, queued, "cancelled", fold.CAUSE_CANCEL)
        kernel.control.cancel(holder)
        assert support.wait_until(
            lambda: _state(kernel, holder) == "cancelled",
            tolerances.JOIN_WAIT_S + clock.stop_bound,
        )


def test_queued_deadline_stop_row_length_zero_timed_out() -> None:
    kernel = support.spine_kernel()
    # the pool size is read from the home's config.toml at each admission (v0.4 rule 11)
    (kernel.home / "config.toml").write_text("max_running_runs = 1\n", encoding="utf-8")
    holder = _submit(kernel, LONG_S)
    with support.reaping(holder):
        assert support.wait_until(
            lambda: _state(kernel, holder) == "running",
            tolerances.JOIN_WAIT_S,
        )
        with harness.patch_snapshot(kernel, "slow", timeout_s=support.SHORT_DEADLINE_S):
            queued = _submit(kernel, LONG_S)
        assert support.wait_until(
            lambda: _state(kernel, queued) == "timed_out",
            support.SHORT_DEADLINE_S + tolerances.JOIN_WAIT_S,
        )
        _assert_finalized_unspawned(kernel, queued, "timed_out", fold.CAUSE_RELEASE_POINT)
        kernel.control.cancel(holder)


def test_u2_waits_release_slice_then_grace_then_kill() -> None:
    """After the stop row U2 waits at most the release slice, then SIGTERM, `grace`, SIGKILL: the
    tree's SIGTERM-ignoring child logs when the signal arrives, so the wait is measured."""
    kernel = support.spine_kernel()
    order = support.admit_order(kernel, "tree", {"seconds": LONG_S, "hold_term": True})
    run_dir = support.run_dir_of(kernel, order.run_id)
    _set_release_slice(run_dir, SLICE_S)
    thread = support.drive_in_thread(kernel, order)
    with support.reaping(order.run_id):
        support.wait_ready(run_dir)
        requested = time.time()
        kernel.control.cancel(order.run_id)
        assert _terminal(run_dir, thread) == "cancelled"
        ended = time.time()
        term_log = run_dir / "work" / "tmp" / "term_at"
        assert term_log.exists(), "SIGTERM never reached the tree"
        term_at = float(term_log.read_text(encoding="utf-8"))
        # the release slice was waited out (less what the supervisor's poll took to notice) ...
        assert term_at >= requested + SLICE_S - tolerances.SETTLE_SHORT_S
        # ... and no longer than the slice and one poll, plus the tolerance
        assert term_at <= requested + SLICE_S + clock.poll_interval + tolerances.PROC_WAIT_S
        # the whole stop is inside release slice + grace + kill (+ one poll and the tolerance)
        assert ended - requested <= SLICE_S + clock.grace + clock.kill + tolerances.PROC_WAIT_S
        assert not support.marked(order.run_id)


def test_stop_bound_documented() -> None:
    """docs/plugins.md and docs/agents.md publish `stop_bound = release_slice + grace + kill`,
    the three values, the poll interval and the margin, as the plan defaults they are."""
    plugins = (REPO / "docs" / "plugins.md").read_text(encoding="utf-8")
    agents = (REPO / "docs" / "agents.md").read_text(encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if not k.startswith("TRESTLE_")}
    env["PYTHONPATH"] = str(REPO)
    out = subprocess.run(
        [
            sys.executable,
            "-c",
            "from trestle.common import clock as c; print(c.release_slice, c.grace, c.kill, "
            "c.stop_bound, c.poll_interval, c.finalization_margin, c.FINALIZATION_RESERVE_S)",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    release, grace, kill, bound, poll, margin, reserve = (float(v) for v in out)
    assert bound == release + grace + kill
    for doc in (plugins, agents):
        assert "`stop_bound`" in doc and "`release_slice`" in doc
        assert f"{bound:g} s" in doc
        assert "TRESTLE_RELEASE_SLICE_S" in doc
        assert f"{margin:g} s" in doc and f"{reserve:g} s" in doc
    assert "`release_slice` + `grace` + `kill`" in plugins
    assert f"`grace` (default {grace:g} s)" in plugins and f"`kill` (default {kill:g} s)" in plugins
    assert f"`poll_interval` ({poll:g} s)" in plugins
    assert "plan default" in plugins.lower() and "maintainer" in plugins
