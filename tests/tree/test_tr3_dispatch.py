"""L.TR-3.4: the dispatch re-check over a tree, the credential check, and a direct call's deadline.

The dispatch check (B2-C5: before selection, `root_deadline - now` against `worst_case +
release_slice`) covers the whole admitted tree: a tree that fit at admission and no longer fits
after queueing stops before its first ticket, the root's `NodeEnd` `BLOCKED`
`BUDGET_DOES_NOT_FIT` (one spelling, at admission and in-run), every other vertex `NOT_STARTED`.
A child's credential-lifetime check is made against the root's deadline plus margin, never its
slice (V-8 L-3); a unit called directly runs to its own declared deadline, not a child's slice."""

from __future__ import annotations

import json
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from tests.proof import harness, records
from tests.single.workflow import joinkit as jk
from tests.single.workflow import loopkit as kit
from tests.tree import treekit as tk
from trestle.common import clock
from trestle.common.outcome import OutcomeClass
from trestle.common.plan import carving
from trestle.server.ledger import evidence_dir, run_dir_for
from trestle.server.main import Kernel
from trestle.workflow import codes
from trestle.workflow.values import CurrencyFact, NodePath

proves_dispatch = pytest.mark.proves(
    "WR-DEADLINE-3", "WR-DEADLINE-3:tree-dispatch", "A", "tree", "LOGIC", "CI"
)
proves_credential = pytest.mark.proves(
    "WR-UNIT-3", "WR-UNIT-3:credential-vs-root-deadline", "A", "tree", "LOGIC+PROC", "BOTH"
)
proves_direct = pytest.mark.proves(
    "WR-UNIT-3", "WR-UNIT-3:direct-uses-declared-deadline", "A", "tree", "LOGIC+PROC", "BOTH"
)
proves_direct_run = pytest.mark.proves(
    "WR-DEADLINE-1", "WR-DEADLINE-1:tree-direct", "A", "tree", "PROC", "CI"
)


def _barrier_tree(tmp_path: Path) -> tk.TreeRig:
    root = tk.group(
        "app", (tk.bind("left"), tk.bind("right"), tk.bind("join", "left", "right")), concurrency=2
    )
    return tk.tree_rig(tmp_path, root, {n: tk.leaf_unit(n) for n in ("left", "right", "join")})


def _need_s(rig: tk.TreeRig) -> float:
    return carving.worst_case_s(rig.plan, kit.RESERVE_S) + rig.plan.release_slice


def _assert_stopped_at_dispatch(rows: list[dict[str, Any]], ends: dict[str, Any]) -> None:
    assert [row["class"] for row in rows].count("issue") == 0  # zero tickets
    assert [row["class"] for row in rows].count("step") == 0
    root = ends[""]
    assert (root["condition"], root["code"], root["cut"]) == (
        "blocked",
        codes.BUDGET_DOES_NOT_FIT,
        None,
    )
    assert root["human_action"] and root["resend"]  # V-11.1: a human action and a re-send
    for path in ("left", "right", "join"):
        assert (ends[path]["condition"], ends[path]["cut"]) == (None, "not_started"), path


@proves_dispatch
def test_queue_time_misfit_stops_before_first_effect(tmp_path: Path) -> None:
    """In the loop, over the real lane: the tree fit at admission (the rig admitted it), then time
    passed in the queue until less than `worst_case + release_slice` was left."""
    rig = _barrier_tree(tmp_path / "loop")
    rig.rig.clock.advance(tk.TREE_DEADLINE_S - _need_s(rig) + 1)
    rig.run()
    _assert_stopped_at_dispatch(rig.rows(), rig.ends())
    assert rig.marker.calls == []  # zero port calls
    got = tk.answer_of(rig)
    assert got.outcome is OutcomeClass.BLOCKED
    assert got.primary.path == () and got.primary.code == codes.BUDGET_DOES_NOT_FIT
    assert got.primary.human_action and got.primary.resend

    # ... and one second earlier it still fits: the same tree walks
    fits = _barrier_tree(tmp_path / "fits")
    fits.rig.clock.advance(tk.TREE_DEADLINE_S - _need_s(fits) - 1)
    fits.run()
    assert fits.ends()["join"]["condition"] == "satisfied"


@proves_dispatch
def test_queue_time_misfit_through_the_host_finalizes_the_run(
    tree_kernel: Kernel, tmp_path: Path
) -> None:
    """Through the conductor and the child: the run id exists, the run is finalized, and the
    answer is the projected `BLOCKED`, never a `RequestOutcome` refusal (the deadline is moved
    after admission, which is what a queue does to a run that has waited)."""
    path = tmp_path / "plugins" / "two_branch_barrier.py"
    path.write_text(treekit_source("two_branch_barrier"), encoding="utf-8")
    admitted = harness.admit_tree(path, kernel=tree_kernel)
    spec_path = evidence_dir(admitted.run_dir) / "spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    plan = harness_plan(spec)
    need = carving.worst_case_s(plan, clock.FINALIZATION_RESERVE_S) + plan.release_slice
    late = time.time() + need - 1.0  # less than the tree needs remains
    spec["deadline"] = _iso(late)
    spec_path.write_text(json.dumps(spec), encoding="utf-8")

    view = harness.drive_tree(admitted)
    lane = records.lane_rows(admitted.run_dir)
    assert not lane.problems and not lane.torn
    classes = [row.cls for row in lane.rows]
    assert "issue" not in classes and classes.count("end") == 4
    ends = {row.path: row.entry for row in lane.rows if row.cls == "end"}
    assert (ends[""]["condition"], ends[""]["code"]) == ("blocked", codes.BUDGET_DOES_NOT_FIT)
    answer = view.to_dict()["answer"]
    assert answer["outcome"] == "blocked", answer
    assert answer["primary"]["code"] == codes.BUDGET_DOES_NOT_FIT
    assert answer["primary"]["human_action"] and answer["primary"]["resend"]


def treekit_source(name: str) -> str:
    return tk.runnable_source(tk.fixture_source(name))


def harness_plan(spec: dict[str, Any]) -> Any:
    from trestle.common.plan.compiler import AdmittedPlan

    return AdmittedPlan.from_json(json.dumps(spec["plan"]))


def _iso(epoch: float) -> str:
    from datetime import UTC, datetime

    return datetime.fromtimestamp(epoch, tz=UTC).isoformat()


# ---- the credential-lifetime check (WR-ENV-14's Slice A stand-in: a fake read) ----------------


def _credential_rig(tmp_path: Path, valid_until: Any) -> tk.TreeRig:
    """`web` finds its resource ready and reads a credential whose lifetime ends at `valid_until`
    (the fake credential read: one `CurrencyFact`)."""
    unit = tk.leaf_unit("web")

    def observe(u: kit.Unit, params: Any, reads: Any, ctx: Any) -> Any:
        fact = CurrencyFact(jk.DEMO, "g1", valid_until)
        return kit.observation(found=1, ready=True, currency=(fact,))

    web = kit.Unit(unit.decl, observe, unit._advance, unit._release)
    root = tk.group("app", (tk.bind("web"),), concurrency=1)
    rig = tk.tree_rig(tmp_path, root, {"web": web})
    return rig


@proves_credential
def test_credential_check_uses_root_deadline_plus_margin(tmp_path: Path) -> None:
    probe = _credential_rig(tmp_path / "probe", None)
    root_deadline = kit.NOW + timedelta(seconds=tk.TREE_DEADLINE_S)
    margin = timedelta(seconds=kit.MARGIN_S)
    web_slice_end = probe.rig.services.slice_end(NodePath(("web",)))
    assert web_slice_end < root_deadline  # the slice is well inside the root's deadline

    def joined(valid_until: Any, name: str) -> dict[str, Any]:
        rig = _credential_rig(tmp_path / name, valid_until)
        rig.run(host_scope=jk.readings((jk.DEMO, "g1")))
        return rig.ends()["web"]

    # a credential that outlives the root's deadline plus the margin passes ...
    good = joined(root_deadline + margin + timedelta(seconds=1), "good")
    assert good["condition"] == "satisfied"
    # ... one that ends inside the margin does not, though it outlives the child's slice
    assert web_slice_end < root_deadline - timedelta(seconds=5)
    for name, until in (
        ("inside_margin", root_deadline + margin - timedelta(seconds=1)),
        ("past_slice_only", web_slice_end + timedelta(seconds=1)),
    ):
        end = joined(until, name)
        assert end["condition"] in ("blocked", "unsatisfied", "failed"), name  # never satisfied


# ---- a direct call is held to its own declared deadline ---------------------------------------

# The shape of `root_eligible_both`'s `work` (a leaf with no precondition, valid as a root and as a
# child), scaled so that its carved slice is short: budget 10 s, in a tree of budget 20 s and
# deadline 30 s each child is carved 10 s and ends 20 s before the deadline; called directly the
# unit is the root, whose stop is its own release point, 10 s before its deadline. The step is
# real and long (a cooperative wait of LONG_S) and the postcondition holds READY_AFTER_S after the
# first observation: past the slice, before the release point.
WORK_SOURCE = """
from __future__ import annotations

from datetime import timedelta
from typing import Any

from trestle.plugin import Context, trestle
from trestle.workflow import (
    CompletionSource, Compose, LeafDeclaration, LoopFlags, Repeat, WaitPolicy, WorkflowEntry,
)
from trestle.workflow.loop import run_tree
from trestle.workflow.units import NoAction
from trestle.workflow.values import CheckResult, Observation

BUDGET_S = 10
LONG_S = 14
READY_AFTER_S = 16


class Work:
    def __init__(self) -> None:
        self._t0 = None

    def declare(self) -> LeafDeclaration:
        return LeafDeclaration(
            unit="work",
            flags=LoopFlags(Compose.LEAF, CompletionSource.OBSERVED, Repeat.SAFE),
            preconditions=(),
            postcondition="ready",
            wait=WaitPolicy(timedelta(seconds=1), 1.0, timedelta(seconds=8)),
            resource_kind="marker",
            may_touch=frozenset({"marker"}),
            effects=(),
            retryable=frozenset(),
            remedies=(),
            budget=timedelta(seconds=BUDGET_S),
            max_attempts=1,
        )

    def observe(self, params: Any, reads: Any, ctx: Any) -> Any:
        if self._t0 is None:
            self._t0 = ctx.clock.now
        ready = ctx.clock.now >= self._t0 + timedelta(seconds=READY_AFTER_S)
        return Observation(
            present=ready, selector_present=ready, identity_proven=True,
            configuration_compatible=True, postcondition=CheckResult(ready, None, ""),
            preconditions=(), currency=(), found=(), code=None, payload=None,
        )

    def advance(self, params: Any, state: Any, effects: Any, ctx: Any) -> Any:
        ctx.cancellation.wait(timedelta(seconds=LONG_S))  # the long step: it listens, and is long
        return NoAction("work.step_done")

    def release(self, params: Any, handle: Any, effects: Any, ctx: Any) -> Any:
        raise NotImplementedError("no resource")


ENTRY = WorkflowEntry(root="work", units={"work": Work()}, deadline=timedelta(seconds=30))


@trestle(deadline=30)
def direct_work(ctx: Context) -> None:
    run_tree(ctx, ENTRY, {})
"""


def _work_unit() -> Any:
    import types

    module = types.ModuleType("direct_work_source")
    exec(compile(WORK_SOURCE, "<direct_work>", "exec"), module.__dict__)  # noqa: S102
    return module


@proves_direct
def test_direct_call_held_to_declared_deadline(tmp_path: Path) -> None:
    """The same unit inside a tree is cut at the slice MC-23 carves it and is `CARVE_EXCEEDED` when
    its step runs past it; called directly it is a one-vertex root, carved nothing, whose stop is
    the release point of its own declared deadline (later, by the carve's reserve)."""
    module = _work_unit()
    both = tk.group("both", (tk.bind("prep"), tk.bind("work")), concurrency=2, budget_s=20)
    rig = tk.tree_rig(
        tmp_path,
        both,
        {"prep": tk.leaf_unit("prep", budget_s=10), "work": module.Work()},
        deadline_s=30.0,
    )
    in_tree = rig.rig.services.slice_end(NodePath(("work",)))
    direct = kit.admit(module.ENTRY, 30.0)
    assert direct.slices == {}  # a one-vertex root is carved nothing
    direct_stop = kit.NOW + timedelta(seconds=30.0 - direct.release_slice)
    assert in_tree < direct_stop  # the slice ends before the root's own release point
    assert in_tree == kit.NOW + timedelta(seconds=10)  # 30 s deadline - release slice - reserve
    rig.run()
    slow = rig.ends()["work"]
    assert (slow["condition"], slow["code"]) == ("failed", codes.CARVE_EXCEEDED)


@proves_direct_run
def test_direct_call_runs_past_child_slice_to_declared_deadline_proc(
    tree_kernel: Kernel, tmp_path: Path
) -> None:
    """A real run through `ControlSurface.run`: the unit is the root, its step is really long (a
    cooperative wait of `LONG_S`), and it is not cut where the tree's carve would have cut it: it
    reaches its postcondition after the slice offset and before its own release point, with no
    `CARVE_EXCEEDED` in its record. Bounds are the published clock's."""
    plugins = tmp_path / "plugins"
    (plugins / "direct_work.py").write_text(WORK_SOURCE, encoding="utf-8")
    tree_kernel.registry.maybe_refresh()
    assert tree_kernel.registry.get("direct_work") is not None
    result = tree_kernel.control.run(plugin="direct_work", args={}, wait_ms=0)
    run_id = result.run_id
    view = tree_kernel.control.project.await_terminal(run_id)
    run_dir = run_dir_for(tree_kernel.home, run_id)
    lane = records.lane_rows(run_dir)
    assert not lane.problems and not lane.torn
    (end,) = [row.entry for row in lane.rows if row.cls == "end"]
    assert (end["condition"], end["code"]) == ("satisfied", None), end
    steps = [row.entry for row in lane.rows if row.cls == "step"]
    assert [step["code"] for step in steps] == ["work.step_done"]  # no CARVE_EXCEEDED, no failure
    assert view.to_dict()["answer"]["outcome"] == "passed"

    spec = json.loads((evidence_dir(run_dir) / "spec.json").read_text(encoding="utf-8"))
    from datetime import datetime

    deadline = datetime.fromisoformat(spec["deadline"])
    ended = datetime.fromisoformat(end["at"])
    slice_offset = timedelta(seconds=20)  # where the tree's carve ends this unit (10 s + reserve)
    release_point = timedelta(seconds=clock.release_slice)
    assert deadline - ended < slice_offset, "it ran past the child's slice"
    assert deadline - ended > release_point, "and stopped no later than its own release point"
