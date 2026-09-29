"""Slice carving and the finalization margin (MC-23 `carve`; L.SV-3.2).

The budget half of B2-C2, pure and clock-free: every duration is a number of seconds handed in by
the caller (`Admission.admit`, the loop's dispatch re-check, the leaf budget refusal), so the same
plan and limits always give the same slices or the same refusal.

`carve(plan, deadline_s, reserve_s, release_slice_s)` is B2-C2 (4). Reading the plan's vertices
(V-1.2: one vertex per logical node, a shared node listed by each of its parents):

* a vertex's *effective budget* is its declared budget, else its carve; the root's carve is the
  admitted deadline less this root's release slice (the root has no carve of its own, V-8 L-3);
* a vertex's *carve* is `effective(parent) - reserve_s`, the minimum over every parent, so a
  shared node is carved at the minimum and checked against every parent (V-8 L-3);
* every child's budget must be <= its parent's effective budget - `reserve_s`;
* an `all` composite's budget must also be >= its longest needs-chain of child budgets and
  >= ceil(width / concurrency) * its largest child budget;
* the root's budget + this root's release slice must be <= the admitted deadline, which must be
  <= `deadline_ceiling_s` when the caller passes one;
* the worst case over `choice` alternatives: only one alternative runs, so every alternative must
  fit and a choice without a declared budget needs its largest alternative's.

A child with no declared budget contributes what it needs to its parent: nothing for a leaf, its
own worst case for a composite (plus the parent's reserve, since a child must fit within
`parent - reserve`). A misfit is `Refusal(admission.budget_does_not_fit, node)`, naming the child
whose budget does not fit its parent, else the composite whose own budget is too small, else the
root. Carves are anchored backwards from the root deadline (B2-C5): the root ends at
`deadline_s - release_slice_s`, a child ends `reserve_s` before its parent does and early enough
for each sibling that needs it to run its whole budget after it.

`margin_needed(plan, limits)` is B2-C2 (5): the worst-case finalization the host needs after the
admitted deadline, `grace + kill + sum over release ranks of ceil(targets_in_rank /
sweep_parallelism) * 5 * max(release_timeout in the rank)`, one target per CREATE+RUN effect in
scope (observe, stop, remove, observe, settle: five descriptor-bounded steps).

`release_slice_for` is B2-C1's rule: a root with no declared release walk, and a plain plugin
(the implicit depth-1 plan), gets no release slice.

Imports the standard library and `trestle.common.plan` only.
"""

from __future__ import annotations

import math
from collections import defaultdict, deque
from dataclasses import dataclass, replace
from typing import final

from trestle.common.plan import formats
from trestle.common.plan import vocabulary as vocab
from trestle.common.plan.compiler import AdmittedPlan, Refusal, Vertex
from trestle.common.plan.declared import ROOT_PATH

SWEEP_STEPS = 5  # observe, stop, remove, observe, settle (B2-C2 (5))

_ROOT_NAME = "<root>"


@final
@dataclass(frozen=True, slots=True)
class Slice:
    """One non-root vertex's carve.

    `budget_s` is the slice the parent grants, in seconds: the minimum over every parent of
    `effective(parent) - reserve`. `end_s` is where the slice ends, in seconds after the admission
    instant, anchored backwards from the admitted deadline (B2-C5); `RunServices.slice_end(path)`
    adds it to the admission instant.
    """

    budget_s: float
    end_s: float


@final
@dataclass(frozen=True, slots=True)
class MarginLimits:
    """The operator limits `margin_needed` reads (`OperatorLimits`, seconds; MC-09)."""

    grace: float
    kill: float
    sweep_parallelism: int


def _name(path: str) -> str:
    return path if path != ROOT_PATH else _ROOT_NAME


def _misfit(path: str, message: str) -> Refusal:
    return Refusal(vocab.BUDGET_DOES_NOT_FIT, _name(path), message=message)


def release_slice_for(plan: AdmittedPlan, published_s: float) -> float:
    """This root's release slice: `published_s` (`OperatorLimits.release_slice`) when the plan has
    a release walk (some vertex declares a CREATE+RUN effect), else 0 (B2-C1: its release point is
    its admitted deadline). The implicit depth-1 plan declares none."""
    if published_s < 0:
        raise ValueError("release slice must not be negative")
    return published_s if any(v.create_run for v in plan.vertices) else 0.0


class _Graph:
    """Containment and needs structure of a plan's vertices."""

    def __init__(self, plan: AdmittedPlan) -> None:
        self.by_path: dict[str, Vertex] = {v.path: v for v in plan.vertices}
        self.parents: dict[str, list[str]] = defaultdict(list)
        for v in plan.vertices:
            for child in v.children:
                if child in self.by_path:
                    self.parents[child].append(v.path)
        self.dependents: dict[str, list[str]] = defaultdict(list)
        self.dependencies: dict[str, list[str]] = defaultdict(list)
        for dependency, dependent in plan.edges:
            self.dependents[dependency].append(dependent)
            self.dependencies[dependent].append(dependency)
        # parents before children (containment), for the top-down carve
        self.down = self._order(
            {p: [c for c in self.by_path[p].children if c in self.by_path] for p in self.by_path}
        )
        self.up = list(reversed(self.down))

    @staticmethod
    def _order(succ: dict[str, list[str]]) -> list[str]:
        indeg = {p: 0 for p in succ}
        for outs in succ.values():
            for q in outs:
                indeg[q] += 1
        ready = deque(p for p, n in indeg.items() if n == 0)
        out: list[str] = []
        while ready:
            p = ready.popleft()
            out.append(p)
            for q in succ[p]:
                indeg[q] -= 1
                if indeg[q] == 0:
                    ready.append(q)
        return out  # a containment cycle is compile's DEPENDENCY_CYCLE: never reached here


def _chain(kids: list[str], edges: set[tuple[str, str]], weight: dict[str, float]) -> float:
    """The longest needs-chain among `kids`: the largest sum of `weight` along a path of
    (dependency, dependent) edges whose ends are both in `kids`."""
    inside = set(kids)
    dependents: dict[str, list[str]] = defaultdict(list)
    for dependency, dependent in edges:
        if dependency in inside and dependent in inside:
            dependents[dependency].append(dependent)
    memo: dict[str, float] = {}

    def longest(path: str) -> float:
        if path not in memo:
            memo[path] = weight[path] + max((longest(d) for d in dependents[path]), default=0.0)
        return memo[path]

    return max((longest(k) for k in kids), default=0.0)


def _needs(plan: AdmittedPlan, graph: _Graph, reserve_s: float) -> dict[str, float]:
    """`contrib(v)`: what a vertex asks of its parent: its declared budget, else what it needs
    (a leaf nothing; a choice its largest alternative's; an `all` its chain, its width term or its
    largest child's + the reserve, whichever is greatest)."""
    edges = set(plan.edges)
    contrib: dict[str, float] = {}
    for path in graph.up:  # children before parents
        v = graph.by_path[path]
        if v.budget_s is not None:
            contrib[path] = float(v.budget_s)
            continue
        kids = [c for c in v.children if c in graph.by_path]
        if not kids:
            contrib[path] = 0.0
            continue
        largest = max(contrib[k] for k in kids)
        need = largest + reserve_s if largest > 0 else 0.0
        if v.compose == "all":
            width = math.ceil(len(kids) / max(v.concurrency, 1)) * largest
            need = max(need, _chain(kids, edges, contrib), width)
        contrib[path] = need
    return contrib


def worst_case_s(plan: AdmittedPlan, reserve_s: float) -> float:
    """The worst-case tree budget in seconds: the root's declared budget, else what it needs,
    over every choice alternative (`PlanAccepted.worst_case`)."""
    if reserve_s < 0:
        raise ValueError("reserve must not be negative")
    return _needs(plan, _Graph(plan), reserve_s)[ROOT_PATH]


def carve(
    plan: AdmittedPlan,
    deadline_s: float,
    reserve_s: float,
    release_slice_s: float,
    *,
    deadline_ceiling_s: float | None = None,
) -> dict[str, Slice] | Refusal:
    """B2-C2 (4): the slice of every non-root vertex, or `BUDGET_DOES_NOT_FIT` naming the node.

    `deadline_s` is the admitted deadline as seconds from admission, `reserve_s` the reserve each
    parent holds back, `release_slice_s` this root's release slice (`release_slice_for`); with
    `deadline_ceiling_s` (`OperatorLimits.deadline_ceiling`) the deadline is also bounded by it.
    Pure; never raises for a compiled plan and non-negative limits.
    """
    if reserve_s < 0 or release_slice_s < 0 or deadline_s < 0:
        raise ValueError("deadline, reserve and release slice must not be negative")
    graph = _Graph(plan)
    root = graph.by_path[ROOT_PATH]
    if deadline_ceiling_s is not None and deadline_s > deadline_ceiling_s:
        return _misfit(root.path, "deadline over the ceiling")
    root_end = deadline_s - release_slice_s
    if root_end < 0:
        return _misfit(root.path, "release slice over the deadline")
    contrib = _needs(plan, graph, reserve_s)
    if root.budget_s is not None and root.budget_s + release_slice_s > deadline_s:
        return _misfit(root.path, "root budget and release slice over the deadline")

    effective: dict[str, float] = {}
    carve_of: dict[str, float] = {}
    for path in graph.down:  # parents before children
        v = graph.by_path[path]
        if path == ROOT_PATH:
            carve_of[path] = root_end
        else:
            carve_of[path] = min(effective[p] for p in graph.parents[path]) - reserve_s
        effective[path] = float(v.budget_s) if v.budget_s is not None else carve_of[path]

    for path in graph.down:
        v = graph.by_path[path]
        kids = [c for c in v.children if c in graph.by_path]
        for child in kids:  # a shared child is met once per parent
            if contrib[child] > effective[path] - reserve_s:
                return _misfit(child, "budget over the parent's budget less its reserve")
        if v.compose == "all" and kids:
            largest = max(contrib[k] for k in kids)
            chain = _chain(kids, set(plan.edges), contrib)
            width = math.ceil(len(kids) / max(v.concurrency, 1)) * largest
            if max(chain, width) > effective[path]:
                return _misfit(path, "budget under its needs-chain or its width")

    end: dict[str, float] = {}
    # ends: a child ends `reserve_s` before each parent, and early enough for every dependent
    # sibling to run its whole budget after it (backwards from the root deadline, B2-C5)
    succ: dict[str, list[str]] = {p: [] for p in graph.by_path}
    for path, v in graph.by_path.items():
        for child in v.children:
            if child in graph.by_path:
                succ[path].append(child)
    for dependency, dependent in plan.edges:
        if dependency in succ and dependent in graph.by_path:
            succ[dependent].append(dependency)
    for path in _Graph._order(succ):
        if path == ROOT_PATH:
            end[path] = root_end
            continue
        bounds = [end[p] - reserve_s for p in graph.parents[path] if p in end]
        bounds += [end[d] - contrib[d] for d in graph.dependents[path] if d in end]
        end[path] = min(bounds)
    return {
        path: Slice(budget_s=carve_of[path], end_s=end[path])
        for path in graph.down
        if path != ROOT_PATH
    }


def attach(plan: AdmittedPlan, slices: dict[str, Slice], release_slice_s: float) -> AdmittedPlan:
    """The plan with its carve attached: `slices` holds each slice's budget in seconds, and the
    digest is recomputed (it covers `slices` and `release_slice`)."""
    attached = replace(
        plan,
        slices={path: s.budget_s for path, s in slices.items()},
        release_slice=release_slice_s,
    )
    return replace(attached, plan_digest=formats.plan_digest(attached.body()))


def margin_needed(plan: AdmittedPlan, limits: MarginLimits) -> float:
    """B2-C2 (5): `grace + kill + sum over release ranks of ceil(targets_in_rank /
    sweep_parallelism) * 5 * max(release_timeout of the rank's targets)`, in seconds. One target
    per CREATE+RUN effect of every vertex in scope (all choice alternatives: the worst case); an
    effect with no declared release timeout adds nothing (publication refuses that
    declaration)."""
    if limits.sweep_parallelism < 1:
        raise ValueError("sweep_parallelism must be at least 1")
    ranks: dict[int, list[float]] = defaultdict(list)
    for v in plan.vertices:
        for _effect, timeout in v.create_run:
            ranks[plan.release_rank[v.path]].append(0.0 if timeout is None else float(timeout))
    sweep = sum(
        math.ceil(len(timeouts) / limits.sweep_parallelism) * SWEEP_STEPS * max(timeouts)
        for timeouts in ranks.values()
    )
    return limits.grace + limits.kill + sweep
