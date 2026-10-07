"""L.TR-4.4: lease inheritance at run time (WR-UNIT-4).

A tree's lease is the ROOT's: it is taken at the root's admission (`created` row `lease_key`, SL-8),
held by the root run until its terminal row, and every child runs under it. A child has no lease
verb of its own: no acquire, no queue entry, no release, in the ledger, in the lane or in the
scheduler. A unit called directly is a root: it takes the lease before its first effect, so a tree
root on E and a direct call on E queue FIFO behind each other and never mutate together.

Real runs through the host: the tree is admitted by the harness (no tree is admissible through
`ControlSurface.run` before TR-L, MC-B3-03) with the holder index the admission would have written
(`Admission.admit` adds it), and handed to the scheduler as `ControlSurface.run` does; the direct
call goes through `ControlSurface.run`. The plugins mutate by appending timestamped `start` and
`end` events to the file named by `LEASE_LOG` around a step that holds until the run's `go` file
exists, so a test reads each run's mutation interval from a record it can order."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from tests.core.spine import support
from tests.proof import harness, records, tolerances
from trestle.common.plan.declared import canonical_json
from trestle.common.types import RunView
from trestle.server import pool as pools
from trestle.server.home import read_marker
from trestle.server.ledger import run_dir_for
from trestle.server.main import Kernel

ENV = "prod"
KEY = canonical_json(ENV)

proves_no_entry = pytest.mark.proves(
    "WR-UNIT-4", "WR-UNIT-4:no-child-lease-entry", "A", "tree", "PROC+LOGIC", "CI"
)
proves_not_queued = pytest.mark.proves(
    "WR-UNIT-4", "WR-UNIT-4:child-never-queued-behind-root", "A", "tree", "PROC+LOGIC", "CI"
)
proves_direct = pytest.mark.proves(
    "WR-UNIT-4", "WR-UNIT-4:direct-call-takes-lease", "A", "tree", "PROC+LOGIC", "CI"
)
proves_overlap = pytest.mark.proves(
    "WR-UNIT-4", "WR-UNIT-4:no-overlap-root-vs-direct", "A", "tree", "PROC+LOGIC", "CI"
)

# One plugin source, two shapes. `Work` is a leaf whose one step logs `start`, tells the test it is
# active (`ready-<unit>` in the run's tmp), holds until `go` appears there, logs `end` and returns.
# `tree` is a root on the environment with two independent children (`first`, `second`, concurrency
# 2, no environment of their own); `direct` is the same unit called as a one-vertex root.
SOURCE = """
from __future__ import annotations

import os
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

from trestle.plugin import Context, trestle
from trestle.workflow import (
    AllDeclaration, ChildBinding, CompletionSource, Compose, LeafDeclaration, LoopFlags, Repeat,
    WaitPolicy, WorkflowEntry,
)
from trestle.workflow.loop import run_tree
from trestle.workflow.units import NoAction
from trestle.workflow.values import CheckResult, Observation

TAG = "__TAG__"
POLL_S = __POLL__
STATE: dict[str, Any] = {}


def log(event: str, unit: str) -> None:
    with open(os.environ["LEASE_LOG"], "a", encoding="utf-8") as sink:
        sink.write(f"{time.time():.6f} {event} {TAG} {unit}\\n")


class Work:
    def __init__(self, unit: str, env_key: str | None) -> None:
        self._unit = unit
        self._env_key = env_key

    def declare(self) -> LeafDeclaration:
        return LeafDeclaration(
            unit=self._unit,
            flags=LoopFlags(Compose.LEAF, CompletionSource.OBSERVED, Repeat.SAFE),
            preconditions=(),
            postcondition="ready",
            wait=WaitPolicy(timedelta(seconds=1), 1.0, timedelta(seconds=8)),
            resource_kind="marker",
            may_touch=frozenset({"marker"}),
            effects=(),
            retryable=frozenset(),
            remedies=(),
            budget=timedelta(seconds=30),
            max_attempts=1,
            env_key_field=self._env_key,
        )

    def observe(self, params: Any, reads: Any, ctx: Any) -> Any:
        ready = (STATE["tmp"] / f"done-{self._unit}").exists()
        return Observation(
            present=ready, selector_present=ready, identity_proven=True,
            configuration_compatible=True, postcondition=CheckResult(ready, None, ""),
            preconditions=(), currency=(), found=(), code=None, payload=None,
        )

    def advance(self, params: Any, state: Any, effects: Any, ctx: Any) -> Any:
        tmp = STATE["tmp"]
        log("start", self._unit)
        (tmp / f"ready-{self._unit}").write_text("1", encoding="utf-8")
        while not (tmp / "go").exists():
            if ctx.cancellation.wait(timedelta(seconds=POLL_S)):
                break
        log("end", self._unit)
        (tmp / f"done-{self._unit}").write_text("1", encoding="utf-8")
        return NoAction("work.step_done")

    def release(self, params: Any, handle: Any, effects: Any, ctx: Any) -> Any:
        raise NotImplementedError("no resource")


__ENTRY__

@trestle(deadline=__DEADLINE__, env_arg="env")
def __NAME__(ctx: Context, env: str = "dev") -> dict[str, str]:
    STATE["tmp"] = ctx.tmp
    run_tree(ctx, ENTRY, {"env": env})
    return {"env": env}
"""

TREE_ENTRY = """
ENTRY = WorkflowEntry(
    root="pair",
    units={
        "pair": AllDeclaration(
            unit="pair",
            flags=LoopFlags(Compose.ALL, CompletionSource.OBSERVED, Repeat.SAFE),
            children=(
                ChildBinding(unit="first", params={}, needs=()),
                ChildBinding(unit="second", params={}, needs=()),
            ),
            concurrency=2,
            budget=timedelta(seconds=60),
            identifier_sets={},
            arg_bindings=(),
            env_key_field="env",
        ),
        "first": Work("first", None),
        "second": Work("second", None),
    },
    deadline=timedelta(seconds=__DEADLINE__),
)

"""

DIRECT_ENTRY = """
ENTRY = WorkflowEntry(
    root="only", units={"only": Work("only", "env")}, deadline=timedelta(seconds=__DEADLINE__)
)

"""

# The tree is admitted by the harness, which skips the busy pre-check; a direct call goes through
# `Admission.admit`, which refuses a request that a holder's deadline leaves too little room, so
# the direct plugin's deadline is the later one (the same relation a queued request has).
TREE_DEADLINE_S = 300
DIRECT_DEADLINE_S = 600


def source(name: str, entry: str, deadline_s: int) -> str:
    return (
        SOURCE.replace("__ENTRY__", entry)
        .replace("__TAG__", name)
        .replace("__POLL__", repr(tolerances.POLL_FINE_S))
        .replace("__DEADLINE__", str(deadline_s))
        .replace("__NAME__", name)
    )


@pytest.fixture
def leased(tree_kernel: Kernel, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Kernel:
    """The kernel with the two plugins published and the event log named."""
    log = tmp_path / "lease.log"
    log.write_text("", encoding="utf-8")
    monkeypatch.setenv("LEASE_LOG", str(log))
    plugins = tmp_path / "plugins"
    (plugins / "lease_tree.py").write_text(
        source("lease_tree", TREE_ENTRY, TREE_DEADLINE_S), encoding="utf-8"
    )
    (plugins / "lease_direct.py").write_text(
        source("lease_direct", DIRECT_ENTRY, DIRECT_DEADLINE_S), encoding="utf-8"
    )
    tree_kernel.registry.maybe_refresh()
    for name in ("lease_tree", "lease_direct"):
        assert tree_kernel.registry.get(name) is not None
    return tree_kernel


# ---- helpers ----------------------------------------------------------------------------------


def events(tmp_path: Path) -> list[tuple[float, str, str, str]]:
    """The event log, in order: (time, `start`|`end`, plugin tag, unit)."""
    rows = []
    for line in (tmp_path / "lease.log").read_text(encoding="utf-8").splitlines():
        at, event, tag, unit = line.split()
        rows.append((float(at), event, tag, unit))
    return rows


def interval(tmp_path: Path, tag: str) -> tuple[float, float]:
    """A plugin run's mutation interval: its first `start` to its last `end`."""
    mine = [row for row in events(tmp_path) if row[2] == tag]
    starts = [at for at, event, _, _ in mine if event == "start"]
    ends = [at for at, event, _, _ in mine if event == "end"]
    assert starts and ends, mine
    return min(starts), max(ends)


def start_tree(kernel: Kernel, args: dict[str, Any] | None = None) -> harness.AdmittedTree:
    """Admit `lease_tree` as `Admission.admit` would (its live marker carries the key) and hand it
    to the scheduler as `ControlSurface.run` does, without waiting."""
    admitted = harness.admit_tree(
        kernel.registry.plugin_dirs[0] / "lease_tree.py", args or {"env": ENV}, kernel=kernel
    )
    marker = read_marker(kernel.home, admitted.run_id)
    assert marker is not None and marker["lease_key"] == KEY, marker
    kernel.control._drive_background(admitted.order)
    return admitted


def start_direct(kernel: Kernel) -> tuple[str, Path]:
    view = kernel.control.run("lease_direct", {"env": ENV}, wait_ms=0)
    assert isinstance(view, RunView), view
    return view.run_id, run_dir_for(kernel.home, view.run_id)


def tmp_of(run_dir: Path) -> Path:
    return run_dir / "work" / "tmp"


def ready(run_dir: Path, *units: str) -> bool:
    return all((tmp_of(run_dir) / f"ready-{unit}").exists() for unit in units)


def let_go(run_dir: Path) -> None:
    tmp_of(run_dir).mkdir(parents=True, exist_ok=True)
    (tmp_of(run_dir) / "go").write_text("1", encoding="utf-8")


def kinds(run_dir: Path) -> list[str]:
    return [str(row["kind"]) for row in support.rows(run_dir)]


def finish(kernel: Kernel, run_id: str) -> dict[str, Any]:
    view = kernel.control.project.await_terminal(run_id)
    assert isinstance(view, RunView), view
    answer = view.to_dict()["answer"]
    assert answer["outcome"] == "passed", answer
    return answer  # type: ignore[no-any-return]


def waiting_ids(kernel: Kernel) -> list[str]:
    return [entry.order.run_id for entry in kernel.control.scheduler.waiting]


# ---- the proofs -------------------------------------------------------------------------------


@proves_no_entry
def test_child_record_has_no_lease_entry(leased: Kernel) -> None:
    """The root run holds the key and its two children run under it: the record shows the lease
    exactly once, on the root's `created` row, and no lease, acquire, queue or release entry of any
    child; the holder index and the scheduler know the root run only."""
    admitted = start_tree(leased)
    run_id, run_dir = admitted.run_id, admitted.run_dir
    home = leased.home
    scheduler = leased.control.scheduler
    try:
        assert support.wait_until(lambda: ready(run_dir, "first", "second"), tolerances.JOIN_WAIT_S)
        # both children are mutating now, and the one lease is the root's
        assert pools.key_runs(pools.load_sched(home)) == [(run_id, KEY)]
        assert scheduler.running_keys == {run_id: KEY}
        assert not scheduler.waiting
    finally:
        let_go(run_dir)
    finish(leased, run_id)

    words = {"lease", "acquire", "queue", "queued", "queue_entry"}
    lane = records.lane_rows(run_dir)
    assert not lane.problems and not lane.torn
    child_rows = [row for row in lane.rows if row.path]  # a row addressed to a non-root vertex
    assert {row.path for row in child_rows} >= {"first", "second"}
    for row in lane.rows:
        assert not words & {w for key in row.entry for w in key.split("_")}, row.entry
        assert not words & {str(row.entry.get("class")), str(row.entry.get("kind"))}, row.entry
    ledger = support.rows(run_dir)
    with_key = [row for row in ledger if any("lease" in key for key in row)]
    assert [row["kind"] for row in with_key] == ["created"] and with_key[0]["lease_key"] == KEY
    assert not [k for k in kinds(run_dir) if k in words or "lease" in k or "queue" in k]
    # the lease ends with the root's terminal row, and nothing is left held or queued
    assert support.wait_until(
        lambda: pools.key_runs(pools.load_sched(home)) == [], tolerances.JOIN_WAIT_S
    )
    assert scheduler.running_keys == {} and not scheduler.waiting


@proves_not_queued
def test_child_never_waits_behind_root(leased: Kernel) -> None:
    """While the root holds E, another request for E waits in the scheduler, and the root's own
    children start at once (they were never in the queue): the waiting list names the other run
    only, and both children reach their step before that run has started."""
    admitted = start_tree(leased)
    root_dir = admitted.run_dir
    other_id, other_dir = None, None
    try:
        assert support.wait_until(
            lambda: ready(root_dir, "first", "second"), tolerances.JOIN_WAIT_S
        )
        other_id, other_dir = start_direct(leased)
        assert support.wait_until(lambda: waiting_ids(leased) == [other_id], tolerances.JOIN_WAIT_S)
        # the children run while the other request waits; the waiter has no started row
        assert ready(root_dir, "first", "second")
        assert "started" not in kinds(other_dir)
        assert waiting_ids(leased) == [other_id]  # a queue entry is a run, never a child path
        assert leased.control.scheduler.running_keys == {admitted.run_id: KEY}
    finally:
        let_go(root_dir)
        if other_dir is not None:
            let_go(other_dir)
    finish(leased, admitted.run_id)
    assert other_id is not None
    finish(leased, other_id)


@proves_direct
def test_direct_call_acquires_before_first_effect(leased: Kernel, tmp_path: Path) -> None:
    """A unit called directly takes the lease before it mutates anything. Behind a holder it waits:
    no `started` row, no step, no effect; once the holder ends it is dispatched (the lease is its
    own: `running_keys`), and only then does its first effect happen, after the holder's last."""
    admitted = start_tree(leased)
    root_dir = admitted.run_dir
    direct_id, direct_dir = None, None
    try:
        assert support.wait_until(
            lambda: ready(root_dir, "first", "second"), tolerances.JOIN_WAIT_S
        )
        direct_id, direct_dir = start_direct(leased)
        assert support.wait_until(
            lambda: waiting_ids(leased) == [direct_id], tolerances.JOIN_WAIT_S
        )
        time.sleep(tolerances.SETTLE_S)  # long enough for a wrongly started run to show
        assert "started" not in kinds(direct_dir) and not ready(direct_dir, "only")
        assert not [e for e in events(tmp_path) if e[2] == "lease_direct"]
    finally:
        let_go(root_dir)
        if direct_dir is not None:
            let_go(direct_dir)
    finish(leased, admitted.run_id)
    assert direct_id is not None and direct_dir is not None
    assert support.wait_until(lambda: ready(direct_dir, "only"), tolerances.JOIN_WAIT_S)
    finish(leased, direct_id)
    ledger = kinds(direct_dir)
    assert ledger.index("created") < ledger.index("started")  # dispatched = the lease acquired
    root_end = interval(tmp_path, "lease_tree")[1]
    direct_start = interval(tmp_path, "lease_direct")[0]
    assert direct_start > root_end  # its first effect came after the holder's last

    # with nothing held it is dispatched at once: acquisition (the started row) precedes the step
    free_id, free_dir = start_direct(leased)
    try:
        assert support.wait_until(lambda: ready(free_dir, "only"), tolerances.JOIN_WAIT_S)
        assert "started" in kinds(free_dir)  # the step is running, so the lease was taken first
        assert leased.control.scheduler.running_keys == {free_id: KEY}
    finally:
        let_go(free_dir)
    finish(leased, free_id)


@proves_overlap
def test_root_and_direct_call_never_overlap(leased: Kernel, tmp_path: Path) -> None:
    """A tree root on E and a direct call on E, in either arrival order: the second waits for the
    first's terminal row, and the two mutation intervals are disjoint."""
    # the tree first, the direct call queued behind it
    admitted = start_tree(leased)
    tree_dir = admitted.run_dir
    assert support.wait_until(lambda: ready(tree_dir, "first", "second"), tolerances.JOIN_WAIT_S)
    behind_id, behind_dir = start_direct(leased)
    assert support.wait_until(lambda: waiting_ids(leased) == [behind_id], tolerances.JOIN_WAIT_S)
    assert not ready(behind_dir, "only")
    let_go(tree_dir)
    let_go(behind_dir)
    finish(leased, admitted.run_id)
    finish(leased, behind_id)
    _, tree_end = interval(tmp_path, "lease_tree")
    direct_start, direct_end = interval(tmp_path, "lease_direct")
    assert tree_end < direct_start

    # the direct call first, the tree queued behind it
    (tmp_path / "lease.log").write_text("", encoding="utf-8")
    first_id, first_dir = start_direct(leased)
    assert support.wait_until(lambda: ready(first_dir, "only"), tolerances.JOIN_WAIT_S)
    queued = start_tree(leased)
    assert support.wait_until(
        lambda: waiting_ids(leased) == [queued.run_id], tolerances.JOIN_WAIT_S
    )
    assert not ready(queued.run_dir, "first") and not ready(queued.run_dir, "second")
    let_go(first_dir)
    let_go(queued.run_dir)
    finish(leased, first_id)
    finish(leased, queued.run_id)
    direct_start, direct_end = interval(tmp_path, "lease_direct")
    tree_start, _ = interval(tmp_path, "lease_tree")
    assert direct_end < tree_start
    assert leased.control.scheduler.running_keys == {} and not leased.control.scheduler.waiting
