"""L.TR-5.5: termination, exhaustive over the ChoiceNode multi-vertex fake plans, SELECT included.

L.TR-3.7 proved every non-ChoiceNode plan halts. A ChoiceNode adds the selection phase (V-7.2,
B1-O4) before the walk: which alternative is selected (a function of which instances are up), and
what the walk then runs. This suite drives the real loop over the real lane (MC-26's rig, a manual
clock) for every plan `plans.valid_plans("choice")` yields (at run time: `choice_fake`,
`slice_a_tree`, `live_state` and every ChoiceNode fixture a later leaf adds, with no edit here,
A2c2-3), for

* every machine state that reaches a SELECT outcome: for each CHOICE, nothing up (the fallback is
  selected), or exactly one of its alternatives up (that one is selected, else the first);
* every gate outcome: each gate satisfied, or not (the root stops before any effect, B1-E7);
* every assignment of the five leaf classes of L.TR-3.7 to the leaves the walk can reach (a leaf
  the selection leaves out, a gate, and a leaf a route stop or a prerequisite cuts never run, so
  their class is not a choice);
* the goal flips (a cancel) at each of the first waits of a tree whose leaves never turn ready.

Every run must return, write exactly one `NodeEnd` per vertex of the run's walked set (V-4.8),
record the selection the rule of V-7.2 gives (an oracle written here from the declaration alone),
and take at most twice the sum of the leaves' one-vertex bounds in CONVERGE iterations (each
`decide` call is one; `decide` is the loop's only per-node branch and the CHOICE composite never
reaches it, B1-I1). A path past the cap is `Unbounded`: S-8's flip condition, reported and
escalated, never patched (hld-wr-proof KDD 6). Nothing is sampled: enumerated == generated, plan by
plan and run by run."""

from __future__ import annotations

import itertools
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from tests.single.workflow import loopkit as kit
from tests.single.workflow.termination_model import Unbounded
from tests.tree import plans
from tests.tree import treekit as tk
from tests.tree.test_tr3_termination import CANCEL_AT, CLASSES, Counted, leaf_bound
from tests.tree.test_tr3_termination import _unit as scripted_unit
from trestle.common.plan import compiler
from trestle.workflow import loop
from trestle.workflow.decide import Command, decide
from trestle.workflow.declarations import AllDeclaration, ChoiceNode
from trestle.workflow.extract import extract_root
from trestle.workflow.values import Goal, StopCause

pytestmark = pytest.mark.spine

proves = pytest.mark.proves(
    "C-RUNTIME-NEUTRAL", "C-RUNTIME-NEUTRAL:termination-multivertex", "A", "tree", "LOGIC", "CI"
)


@dataclass(frozen=True)
class Shape:
    """One ChoiceNode plan, read from its declaration and the plan admission compiles for it:
    the vertices, what each CHOICE may select, and the gates."""

    plan: plans.Plan
    admitted: compiler.AdmittedPlan
    nodes: dict[str, Any]

    @property
    def vertices(self) -> dict[str, compiler.Vertex]:
        return {v.path: v for v in self.admitted.vertices}

    @property
    def gates(self) -> list[str]:
        """The paths of the gate children (B1-O4): each is only ever observed."""
        out = []
        for node in self.nodes.values():
            for name in node.get("gates", ()):
                out.append(next(c["path"] for c in node["children"] if c["name"] == name))
        return out


def shape_of(plan: plans.Plan) -> Shape:
    _, tree = extract_root(plan.entry)
    admitted = tk.admit_plan(plan.entry, plan.entry.deadline.total_seconds(), plan.request)
    return Shape(plan, admitted, dict(tree.nodes))


def states(shape: Shape) -> list[frozenset[str]]:
    """Every machine state that reaches a SELECT outcome: for each CHOICE (plan order), nothing up
    or exactly one of its eligible alternatives up. The set is the alternatives that are up."""
    options = [
        [None, *shape.admitted.eligible[v.path]]
        for v in shape.admitted.vertices
        if v.compose == "choice"
    ]
    return [frozenset(a for a in combo if a is not None) for combo in itertools.product(*options)]


def selected_by(shape: Shape, up: frozenset[str]) -> dict[str, str]:
    """V-7.2 as an oracle over the declaration: for each CHOICE the walk reaches, the first
    eligible alternative that is up, else the declared fallback if eligible, else the first."""
    chosen: dict[str, str] = {}
    for vertex in shape.admitted.vertices:
        if vertex.compose != "choice" or _left_out(vertex.path, chosen):
            continue
        eligible = shape.admitted.eligible[vertex.path]
        node = shape.nodes[vertex.path]["choice"]
        fallback = next(
            (a["path"] for a in node["alternatives"] if a["unit"] == node["fallback"]), None
        )
        picked = next((a for a in eligible if a in up), None)
        chosen[vertex.path] = picked or (fallback if fallback in eligible else eligible[0])
    return chosen


def _left_out(path: str, chosen: dict[str, str]) -> bool:
    parts = path.split("/") if path else []
    for depth in range(1, len(parts) + 1):
        selected = chosen.get("/".join(parts[: depth - 1]))
        if selected is not None and selected != "/".join(parts[:depth]):
            return True
    return False


def walked(shape: Shape, chosen: dict[str, str]) -> list[str]:
    """V_run: every vertex but the alternatives the selection left out, and what lies under them."""
    return [v.path for v in shape.admitted.vertices if not _left_out(v.path, chosen)]


def route_stopped(shape: Shape, chosen: dict[str, str]) -> set[str]:
    """V-7.3: the dependents whose vantage the alternative selected for what they need does not
    declare (a declared-value membership test, written here from the declaration)."""
    out: set[str] = set()
    for dependency, dependent in shape.admitted.edges:
        if dependency in chosen:
            reach = next(
                a["reachable_from"]
                for a in shape.nodes[dependency]["choice"]["alternatives"]
                if a["path"] == chosen[dependency]
            )
            if shape.vertices[dependent].vantage not in reach:
                out.add(dependent)
    return out


def outcome(schedule: dict[str, str], shape: Shape, path: str, up: frozenset[str]) -> str:
    """How a leaf ends when it starts: an instance that is up is found and ready, so it is satisfied
    whatever its `advance` would have done, unless it never turns ready."""
    kind = schedule[shape.vertices[path].unit]
    return "ok" if path in up and kind != "never_ready" else kind


def dead_leaves(
    shape: Shape,
    live: list[str],
    stopped: set[str],
    schedule: dict[str, str],
    gates: set[str],
    up: frozenset[str],
) -> set[str]:
    """The walked leaves that can never start under a schedule with no goal flip: those lying under
    a route-stopped vertex, and those a `needs` prerequisite (of the leaf or an ancestor, whole
    subtree) that did not pass cuts."""
    vertices = shape.vertices
    leaves = [p for p in live if vertices[p].compose == "leaf"]

    def passes(path: str) -> bool:
        if path in gates:
            return True
        return path not in stopped and outcome(schedule, shape, path, up) == "ok"

    dead: set[str] = set()
    for leaf in leaves:
        if any(leaf == s or leaf.startswith(f"{s}/") for s in stopped if s != leaf):
            dead.add(leaf)
    changed = True
    while changed:
        changed = False
        for leaf in leaves:
            if leaf in dead or leaf in stopped:  # a stopped vertex is ended by its stop
                continue
            parts = leaf.split("/")
            owners = ["/".join(parts[: i + 1]) for i in range(len(parts))]
            needed = {n for o in owners if o in vertices for n in vertices[o].needs}
            if any(
                x in dead or not passes(x)
                for n in needed
                for x in leaves
                if x == n or x.startswith(f"{n}/")
            ):
                dead.add(leaf)
                changed = True
    return dead


def _rig(
    shape: Shape,
    where: Path,
    schedule: dict[str, str],
    up: frozenset[str],
    gates_up: bool,
) -> tk.TreeRig:
    """The plan admitted and ready to walk: each leaf unit scripted as `schedule` says, the
    instances of `up` (and each gate, when `gates_up`) present before the run."""
    entry = shape.plan.entry
    marker = tk.PathMarker()
    marker._live.update(up)  # noqa: SLF001 (the machine before the run)
    if gates_up:
        marker._live.update(shape.gates)  # noqa: SLF001
    behaviour: dict[str, kit.Unit] = {}
    for name, unit in entry.units.items():
        if isinstance(unit, AllDeclaration | ChoiceNode):
            continue
        behaviour[name] = scripted_unit(unit.declare(), schedule[name])  # type: ignore[attr-defined]
    return tk.rig_of_entry(where, entry, marker, behaviour=behaviour, request=shape.plan.request)


def _leaf_units(shape: Shape) -> list[str]:
    return [
        n
        for n, u in shape.plan.entry.units.items()
        if not isinstance(u, AllDeclaration | ChoiceNode)
    ]


def _cap(shape: Shape, rig: tk.TreeRig) -> int:
    """Twice the sum of the one-vertex bounds of the plan's leaf vertices, plus ten."""
    total = 0
    for vertex in rig.plan.vertices:
        if vertex.compose != "leaf":
            continue
        decl = shape.plan.entry.units[vertex.unit].declare()  # type: ignore[attr-defined]
        slice_s = (
            rig.rig.services.slice_end(loop.plan_path(vertex.path)) - kit.NOW
        ).total_seconds()
        total += leaf_bound(decl, max(slice_s, 0.0))
    return 2 * total + 10


def drive(
    shape: Shape,
    rig: tk.TreeRig,
    label: str,
    expected: list[str],
    table: Callable[..., Any] = decide,
) -> tuple[int, dict[str, dict[str, Any]]]:
    """Walk the tree once under the counting `decide`: the CONVERGE iterations it took and the
    `NodeEnd`s it wrote, exactly one per vertex of the walked set (V-4.8, B1-C11)."""
    counted = Counted(_cap(shape, rig), table)
    with mock.patch.object(loop, "decide", counted):
        try:
            rig.run()
        except Unbounded as exc:
            raise Unbounded(f"{label}: {exc}") from exc
    ends = rig.ends()
    assert set(ends) == set(expected), label
    return counted.converge, ends


def _recorded_selection(rig: tk.TreeRig) -> dict[str, str]:
    (plan,) = [r for r in rig.rows() if r["class"] == "plan"]
    return dict(plan["selection"])


def choice_plans() -> list[plans.Plan]:
    return plans.valid_plans("choice")


def expected_runs(shape: Shape) -> int:
    """The size of the product for one plan, from the declaration alone: for each machine state,
    5^(leaves the walk can start and run) with each gate satisfied, plus one run with a gate not."""
    total = 0
    gates = set(shape.gates)
    for up in states(shape):
        chosen = selected_by(shape, up)
        live = walked(shape, chosen)
        stopped = route_stopped(shape, chosen)
        free = [
            p
            for p in live
            if shape.vertices[p].compose == "leaf"
            and p not in gates
            and not any(p == s or p.startswith(f"{s}/") for s in stopped)
        ]
        total += len(CLASSES) ** len(free)
        if gates:
            total += 1
    return total


@proves
def test_termination_exhaustive_choice_plans(tmp_path: Path) -> None:
    """Every plan `valid_plans("choice")` yields x every machine state (every SELECT outcome) x
    every gate outcome x every class assignment halts within the bound, ends the walked set once,
    records the selection V-7.2 gives, and cuts what the schedule says it must: enumerated ==
    generated."""
    yielded = choice_plans()
    generated = [shape_of(p) for p in yielded]
    assert len(generated) == len(yielded)  # no reduction: every ChoiceNode plan is enumerated
    names = {p.name for p in yielded}
    assert {"choice_fake", "slice_a_tree", "live_state"} <= names
    runs = 0
    outcomes: set[tuple[str, str]] = set()
    worst = 0
    for shape in generated:
        plan_runs = 0
        gates = set(shape.gates)
        leaf_units = _leaf_units(shape)
        for up in states(shape):
            chosen = selected_by(shape, up)
            live = walked(shape, chosen)
            stopped = route_stopped(shape, chosen)
            for choice, alt in chosen.items():
                outcomes.add((f"{shape.plan.name}:{choice}", alt))
            free = [
                p
                for p in live
                if shape.vertices[p].compose == "leaf"
                and p not in gates
                and not any(p == s or p.startswith(f"{s}/") for s in stopped)
            ]
            free_units = sorted({shape.vertices[p].unit for p in free})
            for combo in itertools.product(CLASSES, repeat=len(free_units)):
                schedule = {name: "ok" for name in leaf_units}
                schedule.update(zip(free_units, combo, strict=True))
                plan_runs += 1
                where = tmp_path / f"run{runs + plan_runs:06d}"
                label = f"{shape.plan.name}/up={sorted(up)}/{schedule}"
                rig = _rig(shape, where, schedule, up, gates_up=True)
                iterations, ends = drive(shape, rig, label, live)
                worst = max(worst, iterations)
                assert _recorded_selection(rig) == chosen, label  # V-7.2, twice over
                _check_cuts(shape, ends, live, stopped, schedule, gates, up, label)
            if gates:  # a gate that is not satisfied: the root stops, nothing under it starts
                plan_runs += 1
                schedule = {name: "ok" for name in leaf_units}
                rig = _rig(shape, tmp_path / f"run{runs + plan_runs:06d}", schedule, up, False)
                label = f"{shape.plan.name}/up={sorted(up)}/gate down"
                _, ends = drive(shape, rig, label, [v.path for v in shape.admitted.vertices])
                assert (ends[""]["condition"], ends[""]["code"]) == (
                    "failed",
                    "execution.declaration_stale",
                ), label
                assert all(e["cut"] == "not_started" for p, e in ends.items() if p != ""), label
                assert _recorded_selection(rig) == {}, label  # nothing selected before the stop
        assert plan_runs == expected_runs(shape), shape.plan.name  # generated == enumerated
        runs += plan_runs
    assert runs >= 500  # not vacuous
    assert worst >= 3  # ... and some paths are long
    for shape in generated:  # every alternative of every CHOICE is selected in some state
        for choice, alternatives in shape.admitted.eligible.items():
            for alt in alternatives:
                assert (f"{shape.plan.name}:{choice}", alt) in outcomes, (shape.plan.name, alt)


def _check_cuts(
    shape: Shape,
    ends: dict[str, dict[str, Any]],
    live: list[str],
    stopped: set[str],
    schedule: dict[str, str],
    gates: set[str],
    up: frozenset[str],
    label: str,
) -> None:
    vertices = shape.vertices
    for path in stopped:  # V-7.3: blocked with the code, before any ticket
        assert (ends[path]["condition"], ends[path]["code"]) == (
            "blocked",
            "admission.route_unsupported",
        ), label
    if "raises" in schedule.values():  # a raise stops the rest: no closure to state
        return
    dead = dead_leaves(shape, live, stopped, schedule, gates, up)
    for leaf in [p for p in live if vertices[p].compose == "leaf"]:
        end = ends[leaf]
        if leaf in dead:
            assert (end["condition"], end["cut"]) == (None, "not_started"), (label, leaf)
        elif leaf not in stopped:
            assert end["cut"] is None, (label, leaf)
            if (
                "never_ready" not in schedule.values()
                and outcome(schedule, shape, leaf, up) == "ok"
            ):
                assert end["condition"] == "satisfied", (label, leaf)


@proves
@pytest.mark.parametrize("cancel_at", CANCEL_AT)
def test_termination_under_a_goal_flip_at_each_wait(tmp_path: Path, cancel_at: int) -> None:
    """A tree whose leaves never turn ready is stopped by a flag raised at its `cancel_at`-th wait,
    in every SELECT outcome: it halts, ends the walked set once, and every leaf that had not reached
    a condition of its own is cut."""
    flipped = 0
    total = 0
    for n, shape in enumerate(shape_of(p) for p in choice_plans()):
        gate_units = {shape.vertices[g].unit for g in shape.gates}  # a gate that never turns ready
        never = {  # stops the root before the walk (B1-E7): it is satisfied here
            name: "ok" if name in gate_units else "never_ready" for name in _leaf_units(shape)
        }
        for m, up in enumerate(states(shape)):
            chosen = selected_by(shape, up)
            rig = _rig(shape, tmp_path / f"p{n:03d}s{m:03d}", never, up, gates_up=True)
            rig.rig.cancel.stop_on_wait = cancel_at
            rig.rig.cancel.stop_cause = StopCause.CANCEL
            label = f"{shape.plan.name}/up={sorted(up)}/cancel@{cancel_at}"
            iterations, ends = drive(shape, rig, label, walked(shape, chosen))
            assert iterations >= 1
            total += 1
            if len(rig.rig.cancel.waits) >= cancel_at:  # the flag went up: nothing ran on past it
                cut = [e for e in ends.values() if e["cut"] is not None]
                assert cut or all(e["condition"] != "satisfied" for e in ends.values()), label
                flipped += 1
    assert total >= 9 and flipped >= total // 3  # not vacuous: many trees were stopped mid-run


def test_planted_unbounded_row_is_reported_on_a_choice_tree(tmp_path: Path) -> None:
    """S-8's flip condition on a ChoiceNode walk: a table whose UNSATISFIED row neither advances nor
    waits re-joins the same record forever, and the drive reports it unbounded."""
    (plan,) = [p for p in choice_plans() if p.name == "choice_fake"]
    shape = shape_of(plan)
    schedule = {name: "ok" for name in _leaf_units(shape)}

    def planted(flags: Any, condition: Any, goal: Goal) -> Any:
        if goal is Goal.CONVERGE and getattr(condition, "value", None) == "unsatisfied":
            return (Command.REJOIN,)
        return decide(flags, condition, goal)

    live = walked(shape, selected_by(shape, frozenset()))
    with pytest.raises(Unbounded):
        drive(
            shape,
            _rig(shape, tmp_path / "planted", schedule, frozenset(), True),
            "planted",
            live,
            planted,
        )
    drive(shape, _rig(shape, tmp_path / "calm", schedule, frozenset(), True), "calm", live)  # halts


def test_enumerator_yields_every_choice_fixture_with_no_edit_here() -> None:
    """The plan set is read at run time: every ChoiceNode fixture under `tests/fixtures/trees/`
    with a valid label is enumerated (this suite names none), and each one is a plan holding a
    `ChoiceNode` (`choice_long_running` joins at L.TR-5.6 and is covered at the next run)."""
    yielded = choice_plans()
    assert yielded and all(p.kind == "choice" for p in yielded)
    assert all(isinstance(u, ChoiceNode) for p in yielded for u in [_choice_of(p)])


def _choice_of(plan: plans.Plan) -> object:
    return next(u for u in plan.entry.units.values() if isinstance(u, ChoiceNode))
