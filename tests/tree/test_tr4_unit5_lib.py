"""L.TR-4.3: WR-UNIT-5's terminal-path variants, in-library.

Child `c` of `creator_pk` creates a process `P` and a resource `K` (fake in-run markers: `P` is a
real child process of the run, the process table shows it; `K`'s observable is the fake's own
inventory). The tree ends five ways, and each time nothing `c` created may outlive the terminal
answer, `K`'s release must be on the record before the terminal row, and no sibling may have
reported either found (WR-UNIT-5):

- `sibling_fail`, `exception`, `cancel`, `deadline`: a real run through the kernel, the wrapper and
  a child process (MC-26: `admit_tree`, `drive_tree`), read from its lane, its ledger, the process
  table (MC-13's snapshot) and the fake's inventory;
- `restart`: the server dies with the run live, so recovery has to finish it. The run is admitted
  for real; the record a server killed mid-run leaves behind (a `started` row, the identity rows of
  a live leader and `P`, the lane holding `c`'s two claims) is written by the test, `P` and its
  leader are real processes, and `K` is released by the sweep against the sweep stub's engine. The
  true server kill and restart are `L.TR-L.7`'s (MC-12).

The deadline variant shortens the root's own clocks (release slice, reserve, the fixture's budgets)
so the release point falls seconds after admission; a polling child would end itself at its carved
slice, which is earlier, so `c` blocks inside one call and it is the release point that ends it."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from trestle_packs.fakes import FakeMarker, run_scoped_selector

from tests.core.spine import support
from tests.proof import ancestry, harness, records, tolerances
from tests.single.control import sweep_stub as sw
from tests.tree import runs
from tests.tree import treekit as tk
from trestle.child.attempt_lane import TicketRefusal
from trestle.common import clock
from trestle.common import lane_format as lf
from trestle.server import procident
from trestle.server.fold import plan_of_spec
from trestle.server.ledger import TERMINAL_KINDS, RunLedger, evidence_dir, ledger_path
from trestle.server.main import Kernel
from trestle.server.recovery import recover_run_dir

P_EFFECT, K_EFFECT = "spawn_p", "make_k"
HOLD = "import time; time.sleep(3600)"  # a stand-in process that lives until it is signalled
# A leader that starts one such process (argv: the code to run, the run's id) and then holds too.
LEADER = (
    "import subprocess, sys, time; "
    "subprocess.Popen([sys.executable, '-c', sys.argv[1], sys.argv[2]]); time.sleep(3600)"
)

# The mode each live variant asks `creator_pk` for.
MODE = {
    "sibling_fail": "sibling_fail",
    "exception": "exception",
    "cancel": "hold",
    "deadline": "deadline",
}
# What the root's answer says of each (its outcome class and, for a whole-root stop, the stop).
OUTCOME = {
    "sibling_fail": ("failed", None),
    "exception": ("execution_error", None),
    "cancel": ("cancelled", "cancel"),
    "deadline": ("timed_out", "release_point"),
}

# The deadline variant's own clocks: `(constant in the fixture, value)`; a release slice and a
# reserve of one second, so a leaf's budget and the deadline that carries it are a few seconds.
SHORT = [
    ("WAIT_MAX_S = 6", "WAIT_MAX_S = 1"),
    ("RELEASE_TIMEOUT_S = 2", "RELEASE_TIMEOUT_S = 1"),
    ("LEAF_BUDGET_S = 10", "LEAF_BUDGET_S = 3"),
    ("ROOT_BUDGET_S = 20", "ROOT_BUDGET_S = 5"),
    ("DEADLINE_S = 36", "DEADLINE_S = 7"),
    ("deadline=36", "deadline=7"),
]
SHORT_DEADLINE_S = 7
SHORT_RELEASE_S = 1.0


def _proves(variant: str) -> Any:
    label = f"WR-UNIT-5:p-dead-k-released-{variant.replace('_', '-')}"
    marks = [pytest.mark.proves("WR-UNIT-5", label, "A", "tree", "PROC", "CI")]
    if variant == "restart":
        marks.append(
            pytest.mark.proves(
                "WR-UNIT-5", "WR-UNIT-5:recovery-ends-p-and-k", "A", "tree", "PROC", "CI"
            )
        )
    return pytest.param(variant, marks=marks, id=variant)


VARIANTS = [_proves(v) for v in ("sibling_fail", "exception", "cancel", "deadline", "restart")]


def _source(*, short: bool) -> str:
    source = tk.fixture_source("creator_pk")
    if short:
        for old, new in SHORT:
            assert source.count(old) == 1, f"creator_pk: {old!r} moved"
            source = source.replace(old, new)
    return source


def _publish(tmp_path: Path, *, short: bool = False) -> Path:
    path = tmp_path / "plugins" / "creator_pk.py"
    path.write_text(_source(short=short), encoding="utf-8")
    return path


def _markers(run_dir: Path) -> Path:
    return run_dir / "work" / "tmp" / "markers"


def _selectors(run_id: str) -> dict[str, str]:
    """`P`'s and `K`'s run-scoped selectors: what the fake derives from `(root, node, effect)`."""
    lineage = SimpleNamespace(root_run_id=run_id, path=SimpleNamespace(segments=("c",)))
    return {
        "P": run_scoped_selector(lineage, P_EFFECT),
        "K": run_scoped_selector(lineage, K_EFFECT),
    }


def _live_processes(run_dir: Path, run_id: str) -> dict[str, ancestry.ProcInfo]:
    """`P` and `K` as the process table shows them, once both exist (empty until then): read from
    the marker files the fake keeps, which hold each one's pid."""
    found: dict[str, int] = {}
    for label, selector in _selectors(run_id).items():
        path = _markers(run_dir) / f"{selector}.marker"
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if record.get("kind") != "process":
            return {}
        found[label] = int(record["pid"])
    table = {p.pid: p for p in ancestry.snapshot()}
    if not all(pid in table for pid in found.values()):
        return {}
    return {label: table[pid] for label, pid in found.items()}


def _rows(run_dir: Path) -> list[dict[str, Any]]:
    return list(records.ledger_rows(run_dir).rows)


def _terminal(rows: list[dict[str, Any]]) -> int:
    return next(n for n, row in enumerate(rows) if row["kind"] in TERMINAL_KINDS)


def _lane_effects(run_dir: Path) -> dict[int, tuple[str, str]]:
    """Each lane entry's `(class, effect)` by its lane sequence number."""
    lane = records.lane_rows(run_dir)
    assert not lane.problems and not lane.torn, lane.problems
    return {row.entry["seq"]: (row.cls, row.entry.get("effect", "")) for row in lane.rows}


def _release_rows(run_dir: Path) -> dict[str, int]:
    """The ledger index of the row that carries each created resource's release: the folded lane's
    `released` entry for it (the lane is folded into the ledger before the terminal row)."""
    effects = _lane_effects(run_dir)
    found: dict[str, int] = {}
    for n, row in enumerate(_rows(run_dir)):
        if row["kind"] == "lane_folded" and row["entry_class"] == "released":
            found[effects[row["lane_seq"]][1]] = n
    return found


def _gone(before: dict[str, ancestry.ProcInfo]) -> bool:
    return not ancestry.survivors(set(before.values()), ancestry.snapshot())


def _inventory(run_dir: Path) -> frozenset[str]:
    return frozenset(FakeMarker(_markers(run_dir), "run").inventory()["containers"])


def _sibling_looks(run_dir: Path) -> list[dict[str, Any]]:
    """`sib`'s `step.observed` facts, from the run's evidence."""
    events = (evidence_dir(run_dir) / "events.ndjson").read_text(encoding="utf-8").splitlines()
    return [
        e["payload"]
        for e in map(json.loads, events)
        if e["kind"] == "step.observed" and e["payload"]["path"] == "sib"
    ]


def _run_live(
    variant: str, kernel: Kernel, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[harness.AdmittedTree, dict[str, ancestry.ProcInfo], dict[str, Any], float]:
    """Run `creator_pk` to its terminal answer the way `variant` ends it. Returns the run, `P` and
    `K` as seen live, the answer, and the seconds from both existing to the terminal answer."""
    monkeypatch.setattr(clock, "grace", support.TEST_GRACE_S)
    monkeypatch.setattr(clock, "kill", support.TEST_KILL_S)
    short = variant == "deadline"
    if short:  # the run's own clocks, admission's (this process) and the child's (its environment)
        monkeypatch.setattr(clock, "release_slice", SHORT_RELEASE_S)
        monkeypatch.setattr(clock, "FINALIZATION_RESERVE_S", 1.0)
        monkeypatch.setenv("TRESTLE_FINALIZATION_RESERVE_S", "1")
    path = _publish(tmp_path, short=short)
    admitted = harness.admit_tree(path, {"env": "dev", "mode": MODE[variant]}, kernel=kernel)
    views: list[Any] = []
    conductor = threading.Thread(target=lambda: views.append(harness.drive_tree(admitted)))
    started = time.monotonic()
    live: dict[str, ancestry.ProcInfo] = {}
    with support.reaping(admitted.run_id):
        conductor.start()
        try:

            def both_up() -> bool:
                live.update(_live_processes(admitted.run_dir, admitted.run_id))
                return len(live) == 2

            assert support.wait_until(both_up, tolerances.JOIN_WAIT_S), "P and K never both existed"
            up = time.monotonic()
            if variant == "cancel":
                kernel.control.cancel(admitted.run_id)
            conductor.join(timeout=clock.stop_bound + tolerances.JOIN_WAIT_S)
            assert not conductor.is_alive(), "the run never reached its terminal row"
            done = time.monotonic()
        finally:
            ancestry.reap(set(live.values()))  # a failed run must not leave P or K behind
    if variant == "deadline":  # answered within the finalization margin of the admitted deadline
        assert done - started <= SHORT_DEADLINE_S + clock.finalization_margin
    return admitted, live, views[0].to_dict(), done - up


def _assert_live_variant(
    variant: str,
    run: harness.AdmittedTree,
    live: dict[str, ancestry.ProcInfo],
    view: dict[str, Any],
    took: float,
) -> None:
    run_dir = run.run_dir
    outcome, root_stop = OUTCOME[variant]
    answer = view["answer"]
    assert (answer["outcome"], answer["root_stop"]) == (outcome, root_stop)
    # P is gone, inside WR-CANCEL-1's bound of the moment the stop began (MC-13, MC-09)
    assert took <= clock.stop_bound + clock.poll_interval + tolerances.SETTLE_S, took
    assert _gone(live), "P or K is still in the process table at the terminal answer"
    # the fake's inventory agrees: nothing c created is left, and the answer reports it released
    assert _inventory(run_dir) == frozenset()
    assert view["cleanup"]["processes"] == "released"
    # K's release is on the record, before the terminal row, after the root's release phase
    rows = _rows(run_dir)
    releases = _release_rows(run_dir)
    assert set(releases) == {K_EFFECT, P_EFFECT}
    assert max(releases.values()) < _terminal(rows)
    assert releases[K_EFFECT] < releases[P_EFFECT]  # one node: reverse issue order, K first
    lane = records.lane_rows(run_dir)
    ended = max(r.entry["seq"] for r in lane.rows if r.cls == "end")
    assert all(r.entry["seq"] > ended for r in lane.rows if r.cls == "released")
    # each was claimed before it was made: the claim (with its descriptor) precedes the confirmation
    for effect in (P_EFFECT, K_EFFECT):
        mine = [r for r in lane.rows if r.path == "c" and r.entry.get("effect") == effect]
        assert [r.cls for r in mine[:2]] == ["issue", "confirmation"]
        assert mine[0].entry["release"]["form"] == "in_run_group"
    # the sibling saw both, and neither as found: created by this run (WR-UNIT-5)
    looks = _sibling_looks(run_dir)
    assert looks and all(look["found"] == [] for look in looks)
    assert any(
        set(look.get("created_by_run", ())) == set(_selectors(run.run_id).values())
        for look in looks
    )


@pytest.mark.parametrize("variant", VARIANTS)
def test_unit5_variant(
    variant: str, tree_kernel: Kernel, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`P` is gone within the stop bound, `K`'s release record precedes the terminal row, and the
    process table and the fake's inventory taken at the terminal answer show both absent, for a
    failing sibling, `c`'s own exception, a root cancel, the root deadline and a restart."""
    if variant == "restart":
        _restart(tree_kernel, tmp_path, monkeypatch)
        return
    run, live, view, took = _run_live(variant, tree_kernel, tmp_path, monkeypatch)
    _assert_live_variant(variant, run, live, view, took)


def _restart(kernel: Kernel, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The server died with the run live. Recovery ends `P` (and the run's leader, by their
    recorded identities) and releases `K` (by its recorded descriptor), each before the terminal
    row, and the answer is the recovered one."""
    monkeypatch.setattr(clock, "grace", support.TEST_GRACE_S)
    monkeypatch.setattr(clock, "kill", support.TEST_KILL_S)
    admitted = harness.admit_tree(_publish(tmp_path), {"env": "dev", "mode": "hold"}, kernel=kernel)
    run_dir, run_id = admitted.run_dir, admitted.run_id
    ledger = RunLedger.open(ledger_path(run_dir))
    ledger.append("admitted", run_id=run_id, snapshot_id=admitted.order.snapshot_id)
    ledger.append("started", run_id=run_id)

    # the run's processes: a leader (the wrapper's stand-in) that starts P, so P is in its group
    leader = subprocess.Popen([sys.executable, "-c", LEADER, HOLD, run_id], start_new_session=True)
    try:

        def children() -> list[ancestry.ProcInfo]:
            return [q for q in ancestry.snapshot() if q.ppid == leader.pid]

        assert support.wait_until(lambda: len(children()) == 1, tolerances.JOIN_WAIT_S)
        (proc,) = children()
        for pid, is_leader in ((leader.pid, True), (proc.pid, False)):
            start = procident.start_time(pid)
            assert start is not None
            identity = procident.Identity(pid, procident.boot_id(), start, leader.pid, is_leader)
            ledger.append("process_identity", run_id=run_id, **identity.fields())
        live = {"P": proc}

        # what c had done: both creates claimed and applied, the lane left as the killed run had it
        spec = json.loads((evidence_dir(run_dir) / "spec.json").read_text(encoding="utf-8"))
        plan = plan_of_spec(spec)
        assert plan is not None
        lane = runs.lane_of(runs.TreeRun(admitted, plan))
        me = lf.Lineage(run_id, ("c",))
        names = {P_EFFECT: lf.InRunGroup(), K_EFFECT: sw.descriptor("k", exe=sw.EXE)}
        for effect, descriptor in names.items():
            ticket = lane.issue(
                me,
                effect,
                lf.EffectFacetClass.CREATE,
                lf.Repeat.SAFE,
                lf.Lifetime.RUN,
                descriptor,
                2,
                None,
            )
            assert not isinstance(ticket, TicketRefusal), ticket
            lane.confirm(ticket, lf.Confirmation(lf.ConfirmationStatus.APPLIED, None, effect))

        engine = sw.Engine(present={"k"})
        recover_run_dir(run_dir, limits=sw.LIMITS, sweep_io=engine.io())

        leader.wait(timeout=tolerances.PROC_WAIT_S)
        assert _gone(live), "P survived recovery"
    finally:
        if leader.poll() is None:
            leader.kill()
            leader.wait()
        ancestry.reap(support.marked(run_id))

    kinds = [row["kind"] for row in _rows(run_dir)]
    (stop,) = [r for r in _rows(run_dir) if r["kind"] == "group_stop"]
    assert (stop["confirmed_gone"], stop["method"]) == (True, "recovery")  # P ended by identity
    swept = [r for r in _rows(run_dir) if r["kind"] == "sweep_disposition"]
    assert [(r["target"]["path"], r["target"]["effect"], r["disposition"]) for r in swept] == [
        ("c", K_EFFECT, "released")
    ]
    assert engine.roles("k") == ["obs", "stop", "rm", "obs"] and not engine.present  # K gone
    # in this order, and the terminal row last: P ended, the lane folded, K released, then the end
    order = ["group_stop", "lane_folded", "sweep_disposition", "error_record", "evidence_finalized"]
    positions = [kinds.index(kind) for kind in order]
    assert positions == sorted(positions) and kinds[-1] == "interrupted"
    assert positions[-1] < kinds.index("interrupted")
    assert kinds.count("interrupted") == 1
