"""L.TR-3.7: termination, exhaustive over the non-ChoiceNode multi-vertex fake plans (S-8).

The one-vertex suite (`tests/proof/spine/test_termination.py`, L.SV-5.13) proves every walk of one
leaf halts within the loop-owned attempt bound over every reachable `decide` input. A tree adds the
scheduler: which leaves are ready, running, stopped, or cut, under the root's `concurrency` bound.
This suite drives the real `TreeWalk` over the real lane (MC-26's rig, a manual clock) for every
plan `plans.valid_plans("nonchoice")` yields, except a `generators.SCALE_ONLY` key, and for every
fault schedule over its leaves:

* each leaf ends one of five ways (the classes below: satisfied, failed, blocked, raising, never
  ready), every assignment of a class to a leaf, so every joined condition the scheduler can see
  for a node (a lane fault is `blocked`, B2-C7) and every dependency pattern between them;
* the root's goal flips (a cancel) at each of the first waits of a tree whose leaves never turn
  ready, so siblings queued behind the bound, running siblings and dependents are cut mid-run.

Every run must return, write exactly one `NodeEnd` per vertex, and take at most the sum of its
leaves' one-vertex bounds in CONVERGE iterations (each `decide` call is one; every re-advance spends
the node's own loop-owned attempt, V-3.5, so the bound of a tree is linear in its leaves). A path
past the cap is `Unbounded`: S-8's flip condition, reported and escalated, never patched
(hld-wr-proof KDD 6). `hundred_node(100)` is in `valid_plans` but not in this product: it reduces to
`hundred_node(3)` (equal bound B < 3, equal but for the child list), which is enumerated
(A2c5-2, L.TR-0.6's `test_scale_only_reduces_by_sibling_count`)."""

from __future__ import annotations

import itertools
import math
import shutil
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from tests.fixtures.trees import generators
from tests.proof.spine import test_kind_freedom as kind_freedom
from tests.single.workflow import loopkit as kit
from tests.single.workflow.termination_model import Unbounded
from tests.tree import plans
from tests.tree import treekit as tk
from trestle.workflow import loop, ports
from trestle.workflow.decide import Command, decide
from trestle.workflow.declarations import (
    AllDeclaration,
    CompletionSource,
    LeafDeclaration,
)
from trestle.workflow.units import Blocked, Failed
from trestle.workflow.values import Goal, Resend, StopCause

pytestmark = pytest.mark.spine

proves = pytest.mark.proves(
    "C-RUNTIME-NEUTRAL", "C-RUNTIME-NEUTRAL:termination-multivertex", "A", "tree", "LOGIC", "CI"
)

# The five ways a leaf can end, as the scheduler sees them.
CLASSES = ("ok", "failed", "blocked", "raises", "never_ready")
CANCEL_AT = (1, 2, 3)  # the wait (of the first waits any leaf makes) at which a stop flag goes up


def _advance(kind: str) -> Callable[..., Any] | None:
    def failed(unit: Any, params: Any, state: Any, effects: Any, ctx: Any) -> Any:
        return Failed("unit.failed", "no")

    def blocked(unit: Any, params: Any, state: Any, effects: Any, ctx: Any) -> Any:
        return Blocked("unit.blocked", "do the thing", Resend.SUCCEEDS_AFTER_ACTION)

    def raises(unit: Any, params: Any, state: Any, effects: Any, ctx: Any) -> Any:
        raise RuntimeError("the unit died")

    return {"failed": failed, "blocked": blocked, "raises": raises}.get(kind)


def _unit(decl: LeafDeclaration, kind: str) -> kit.Unit:
    """A scripted leaf over `decl` (its own flags, checks and bounds) that ends as `kind` says."""
    if kind == "never_ready":
        base = tk.leaf_unit(decl.unit, declaration=decl)

        def observe(unit: kit.Unit, params: Any, reads: Any, ctx: Any) -> Any:
            seen = reads.read(ports.ResourceReads).observe(kit.SPEC, ctx.lineage, tk.EFFECT)
            return kit.observation(  # its own marker appears once created, and is never ready
                selector_present=seen.selector_present,
                ready=False,
                pre=tuple(True for _ in decl.preconditions),
                preconditions=tuple(decl.preconditions),
            )

        return kit.Unit(base.decl, observe, base._advance, base._release)
    return tk.leaf_unit(decl.unit, declaration=decl, advance=_advance(kind))


def leaf_bound(decl: LeafDeclaration, slice_s: float) -> int:
    """The one-vertex bound (`OneVertexInput.bound`, L.SV-5.13) of a leaf with this declaration,
    its slice `slice_s` seconds long: max_attempts x ceil(max_wait / poll) waiting polls, the polls
    to the slice end, one ADVANCE-only iteration per attempt, one NoAction wait and the first and
    last joins, and each declared remedy's attempts x (1 + ceil(total / poll))."""
    poll = decl.wait.poll_every.total_seconds()
    if decl.flags.completion is CompletionSource.RECORDED:
        return decl.max_attempts + 1
    waits = math.ceil(decl.wait.max_wait.total_seconds() / poll)
    remedies = sum(
        r.attempts * (1 + math.ceil(r.total.total_seconds() / poll)) for r in decl.remedies
    )
    return (
        decl.max_attempts * waits
        + math.ceil(slice_s / poll)
        + decl.max_attempts
        + waits
        + 2
        + remedies
    )


class Counted:
    """`decide` as the loop calls it, counting the CONVERGE calls of the whole tree (the loop's
    walks run on threads) and refusing the one past `cap`."""

    def __init__(self, cap: int, table: Callable[..., Any] = decide) -> None:
        self.cap = cap
        self.converge = 0
        self._table = table  # the decision table the loop consults (a planted one in the self-test)
        self._lock = threading.Lock()

    def __call__(self, flags: Any, condition: Any, goal: Goal) -> Any:
        if goal is Goal.CONVERGE:
            with self._lock:
                self.converge += 1
                over = self.converge > self.cap
            if over:
                raise Unbounded(f"more than {self.cap} CONVERGE iterations: no progress")
        return self._table(flags, condition, goal)


def _rig(plan: plans.Plan, where: Path, schedule: dict[str, str]) -> tk.TreeRig:
    """`plan` admitted and ready to walk, each leaf unit scripted as `schedule` says."""
    entry = plan.entry
    behaviour: dict[str, kit.Unit] = {}
    for name, unit in entry.units.items():
        if isinstance(unit, AllDeclaration):
            continue
        decl = unit.declare()  # type: ignore[attr-defined]
        behaviour[name] = _unit(decl, schedule[name])
    return tk.rig_of_entry(where, entry, behaviour=behaviour, request=plan.request)


def _leaf_units(plan: plans.Plan) -> list[str]:
    return [n for n, u in plan.entry.units.items() if not isinstance(u, AllDeclaration)]


def _cap(plan: plans.Plan, rig: tk.TreeRig) -> int:
    """Twice the sum of the leaves' bounds, over the leaf vertices of the plan (a shared unit is
    one leaf vertex, a unit named by a hundred bindings a hundred)."""
    total = 0
    for vertex in rig.plan.vertices:
        if vertex.compose != "leaf":
            continue
        decl = plan.entry.units[vertex.unit].declare()  # type: ignore[attr-defined]
        slice_s = (
            rig.rig.services.slice_end(loop.plan_path(vertex.path)) - kit.NOW
        ).total_seconds()
        total += leaf_bound(decl, max(slice_s, 0.0))
    return 2 * total + 10


def drive(
    plan: plans.Plan, rig: tk.TreeRig, label: str, table: Callable[..., Any] = decide
) -> tuple[int, dict[str, dict[str, Any]]]:
    """Walk the tree once under the counting `decide`: the CONVERGE iterations it took and the
    `NodeEnd`s it wrote, exactly one per vertex (B1-C11)."""
    counted = Counted(_cap(plan, rig), table)
    with mock.patch.object(loop, "decide", counted):
        try:
            rig.run()
        except Unbounded as exc:
            raise Unbounded(f"{label}: {exc}") from exc
    ends = rig.ends()
    assert set(ends) == {v.path for v in rig.plan.vertices}, label
    return counted.converge, ends


def _non_ok_closure(rig: tk.TreeRig, schedule: dict[str, str]) -> set[str]:
    """The leaf vertices that can never start under a schedule with no goal flip: those a
    `needs` prerequisite (of the leaf or of an ancestor, whole subtree) that is not `ok` cuts."""
    vertices = {v.path: v for v in rig.plan.vertices}
    leaves = [p for p, v in vertices.items() if v.compose == "leaf"]

    def under(path: str) -> list[str]:
        return [x for x in leaves if x == path or x.startswith(path + "/")]

    dead: set[str] = set()
    changed = True
    while changed:
        changed = False
        for leaf in leaves:
            if leaf in dead:
                continue
            parts = leaf.split("/")
            owners = ["/".join(parts[: i + 1]) for i in range(len(parts))]
            needed = {n for o in owners for n in vertices[o].needs}
            broken = any(
                schedule[vertices[x].unit] != "ok" or x in dead for n in needed for x in under(n)
            )
            if broken:
                dead.add(leaf)
                changed = True
    return dead


def product_plans() -> list[plans.Plan]:
    """The plans the fault-schedule product covers: every valid non-ChoiceNode plan but the one
    generated tree `SCALE_ONLY` reduces (`generators.SCALE_ONLY`, A2c5-2)."""
    scale_only = {tree.name for tree in generators.SCALE_ONLY}
    return [p for p in plans.valid_plans("nonchoice") if p.name not in scale_only]


def schedules(names: list[str]) -> list[dict[str, str]]:
    """Every assignment of a class to a leaf unit."""
    return [
        dict(zip(names, combo, strict=True))
        for combo in itertools.product(CLASSES, repeat=len(names))
    ]


@proves
def test_termination_exhaustive_multivertex(tmp_path: Path) -> None:
    """Every plan `valid_plans("nonchoice")` yields (less the `SCALE_ONLY` key) x every class
    assignment x the goal flips halts within the bound, ends every vertex once, and cuts what the
    schedule says it must: enumerated == generated, no sampling."""
    yielded = plans.valid_plans("nonchoice")
    generated = product_plans()
    assert len(generated) == len(yielded) - len(generators.SCALE_ONLY)
    enumerated = 0
    runs = 0
    worst = 0
    for plan in generated:
        enumerated += 1
        names = _leaf_units(plan)
        for schedule in schedules(names):
            runs += 1
            where = tmp_path / f"run{runs:05d}"
            rig = _rig(plan, where, schedule)
            label = f"{plan.name}/{schedule}"
            iterations, ends = drive(plan, rig, label)
            worst = max(worst, iterations)
            vertices = {v.path: v for v in rig.plan.vertices}
            dead = _non_ok_closure(rig, schedule)
            leaves = [p for p, v in vertices.items() if v.compose == "leaf"]
            if "raises" not in schedule.values():  # nothing stops the rest: the closure is exact
                for leaf in leaves:
                    end = ends[leaf]
                    if leaf in dead:
                        assert (end["condition"], end["cut"]) == (None, "not_started"), label
                    else:
                        assert end["cut"] is None, (label, leaf)
                        if all(v != "never_ready" for v in schedule.values()) and (
                            schedule[vertices[leaf].unit] == "ok"
                        ):
                            assert end["condition"] == "satisfied", (label, leaf)
    assert enumerated == len(generated) == len(yielded) - len(generators.SCALE_ONLY)
    assert runs >= 3 * len(generated)  # not vacuous: every plan ran under many schedules
    assert worst >= 3  # ... and some paths are long
    hundred = {p.name for p in yielded}
    assert {t.name for t in generators.SCALE_ONLY} <= hundred  # in `valid_plans`, not the product
    assert not ({t.name for t in generators.SCALE_ONLY} & {p.name for p in generated})
    assert generators.hundred_node(3).name in {p.name for p in generated}


@proves
@pytest.mark.parametrize("cancel_at", CANCEL_AT)
def test_termination_under_a_goal_flip_at_each_wait(tmp_path: Path, cancel_at: int) -> None:
    """A tree whose leaves never turn ready is stopped by a flag raised at its `cancel_at`-th wait
    (queued siblings, running siblings and dependents all in flight): it halts, ends every vertex
    once, and every leaf that had not reached a condition of its own is cut."""
    flipped = 0
    for n, plan in enumerate(product_plans()):
        names = _leaf_units(plan)
        rig = _rig(plan, tmp_path / f"p{n:03d}", {name: "never_ready" for name in names})
        rig.rig.cancel.stop_on_wait = cancel_at
        rig.rig.cancel.stop_cause = StopCause.CANCEL
        iterations, ends = drive(plan, rig, f"{plan.name}/cancel@{cancel_at}")
        assert iterations >= 1
        if len(rig.rig.cancel.waits) >= cancel_at:  # the flag went up: nothing ran on past it
            cut = [e for e in ends.values() if e["cut"] is not None]
            assert cut or all(e["condition"] != "satisfied" for e in ends.values()), plan.name
            flipped += 1
    assert flipped >= len(product_plans()) // 2  # not vacuous: most plans were stopped mid-run


def test_kind_freedom_unchanged() -> None:
    """The tree walk added to `loop.py` branches on no resource kind (V-6.1, B1-I1): the static
    scan of the four library modules is the one-vertex suite's, unchanged and green, and it does
    scan `TreeWalk`."""
    source = (kind_freedom.PACKAGE / "loop.py").read_text(encoding="utf-8")
    assert "class TreeWalk" in source
    for module in kind_freedom.SCOPE:
        assert (
            kind_freedom.scan_source(
                (kind_freedom.PACKAGE / module).read_text(encoding="utf-8"), module
            )
            == []
        ), module
    planted = (
        source + '\n\ndef _planted(spec: object) -> bool:\n    return spec.realization == "x"\n'
    )
    assert kind_freedom.scan_source(planted, "loop.py")  # a kind branch in the tree walk is caught


def test_enumerator_picks_up_planted_fixture(tmp_path: Path) -> None:
    """The enumerator reads the fixture directory at run time: a fixture planted in a temporary
    copy is enumerated with no edit to this suite, and a planted ChoiceNode fixture is classed
    `choice`."""
    copy = tmp_path / "trees"
    shutil.copytree(plans.FIXTURES, copy, ignore=shutil.ignore_patterns("__pycache__"))
    before = {p.name for p in plans.valid_plans("nonchoice", copy)}
    choice_before = {p.name for p in plans.valid_plans("choice", copy)}

    source = (plans.FIXTURES / "two_branch_barrier.py").read_text(encoding="utf-8")
    (copy / "planted_barrier.py").write_text(
        source.replace("two_branch_barrier", "planted_barrier")
    )
    choice_source = (plans.FIXTURES / "choice_fake.py").read_text(encoding="utf-8")
    planted_choice = choice_source.replace("choice_fake", "planted_choice")
    (copy / "planted_choice.py").write_text(planted_choice)
    invalid = source.replace('"expect": "valid"', '"expect": "cycle"')
    (copy / "planted_invalid.py").write_text(
        invalid.replace("two_branch_barrier", "planted_invalid")
    )

    after = {p.name for p in plans.valid_plans("nonchoice", copy)}
    choice_after = {p.name for p in plans.valid_plans("choice", copy)}
    assert after - before == {"planted_barrier"}  # enumerated; the invalid one is not a valid plan
    assert choice_after - choice_before == {"planted_choice"}  # classed as a ChoiceNode plan
    # and the real directory is untouched: nothing was edited to make them show up
    assert "planted_barrier" not in {p.name for p in plans.valid_plans("nonchoice")}


def test_planted_unbounded_row_is_reported_on_a_tree(tmp_path: Path) -> None:
    """S-8's flip condition on the real tree walk: a table whose UNSATISFIED row neither advances
    nor waits re-joins the same record forever, and the drive reports it unbounded (the leaves
    run on threads: the raise in a walk reaches the caller through the scheduler)."""
    (plan,) = [p for p in product_plans() if p.name == "two_branch_barrier"]
    schedule = {name: "ok" for name in _leaf_units(plan)}

    def planted(flags: Any, condition: Any, goal: Goal) -> Any:
        if goal is Goal.CONVERGE and getattr(condition, "value", None) == "unsatisfied":
            return (Command.REJOIN,)
        return decide(flags, condition, goal)

    with pytest.raises(Unbounded):
        drive(plan, _rig(plan, tmp_path / "planted", schedule), "planted", planted)
    # the unplanted table halts the same tree
    drive(plan, _rig(plan, tmp_path / "calm", schedule), "calm")
