"""Property suite for slice carving, the release slice and the finalization margin (MC-23 `carve`;
B2-C2 (4), (5); L.SV-3.2): shape evidence over the generated tree space.

Each property is checked against a brute-force reading of the declaration (all budgets declared,
so an oracle does not repeat `carving`'s bookkeeping for undeclared budgets) or against the
single-alternative variants of a choice."""

from __future__ import annotations

import copy
import itertools
import math
import random
from collections.abc import Callable, Mapping
from typing import Any

from tests.proof import tolerances
from tests.proof.foundations import trees
from tests.proof.foundations.trees import create_run_effect, leaf_node
from trestle.common.plan import vocabulary as vocab
from trestle.common.plan.carving import (
    SWEEP_STEPS,
    MarginLimits,
    Slice,
    attach,
    carve,
    margin_needed,
    release_slice_for,
    worst_case_s,
)
from trestle.common.plan.compiler import AdmittedPlan, Refusal, compile, implicit_depth1_plan
from trestle.common.plan.declared import ROOT_PATH, DeclaredTree

ROOT_NAME = "<root>"
BIG = 10_000.0  # a deadline no generated tree can exceed


def _compile(declared: DeclaredTree) -> AdmittedPlan | None:
    got = compile(declared, {})
    return got if isinstance(got, AdmittedPlan) else None


def _copy_nodes(declared: DeclaredTree) -> dict[str, dict[str, Any]]:
    return {path: copy.deepcopy(dict(node)) for path, node in declared.nodes.items()}


def _rebudget(
    declared: DeclaredTree,
    budget_of: Callable[[str, dict[str, Any]], Any],
    concurrency_of: Callable[[str], int] | None = None,
) -> DeclaredTree:
    """The same tree with every node's `budget` replaced by `budget_of(path, node)` (and every
    `all` composite's concurrency by `concurrency_of(path)` when given)."""
    nodes = _copy_nodes(declared)
    for path, node in nodes.items():
        node["budget"] = budget_of(path, node)
        if concurrency_of is not None and node["compose"] == "all":
            node["concurrency"] = concurrency_of(path)
    return DeclaredTree.build(declared.root, nodes)


def _draw(rng: random.Random, pool: list[float | None]) -> Callable[[str, dict[str, Any]], Any]:
    return lambda _path, _node: rng.choice(pool)


def _draw_int(rng: random.Random, pool: tuple[int, ...]) -> Callable[[str], int]:
    return lambda _path: rng.choice(pool)


def _kids(node: Mapping[str, Any]) -> list[str]:
    if node["compose"] == "all":
        return [c["path"] for c in node["children"]]
    if node["compose"] == "choice":
        return [a["path"] for a in node["choice"]["alternatives"]]
    return []


def _accepting_budgets(declared: DeclaredTree, reserve: float) -> dict[str, float]:
    """Bottom-up budgets every check accepts, with a per-path jitter so that two parents of a
    shared node bound it differently: twice the largest child + the reserve + the jitter."""
    memo: dict[str, float] = {}

    def budget(path: str) -> float:
        if path not in memo:
            node = declared.nodes[path]
            kids = _kids(node)
            jitter = float(sum(map(ord, path)) % 7)
            if not kids:
                memo[path] = 10.0 + jitter
            else:
                memo[path] = 2 * max(budget(k) for k in kids) + reserve + jitter
        return memo[path]

    for path in declared.nodes:
        budget(path)
    return memo


def _accepted(declared: DeclaredTree, reserve: float) -> DeclaredTree:
    budgets = _accepting_budgets(declared, reserve)
    return _rebudget(declared, lambda path, _node: budgets[path])


def _paths_sum(kids: list[str], edges: set[tuple[str, str]], weight: Mapping[str, float]) -> float:
    """Brute force: the largest weight sum over every simple path of needs edges among `kids`."""
    best = 0.0

    def walk(at: str, total: float) -> None:
        nonlocal best
        best = max(best, total)
        for dependency, dependent in edges:
            if dependency == at and dependent in kids:
                walk(dependent, total + weight[dependent])

    for k in kids:
        walk(k, weight[k])
    return best


def _oracle_violations(plan: AdmittedPlan, reserve: float) -> set[str]:
    """Nodes a fully declared plan misfits at, read straight from B2-C2 (4): a child over its
    parent's budget less the reserve is named as the child; a composite under its needs-chain or
    its width term is named as itself."""
    budget = {v.path: float(v.budget_s or 0.0) for v in plan.vertices}
    edges = set(plan.edges)
    bad: set[str] = set()
    for v in plan.vertices:
        for child in v.children:
            if budget[child] > budget[v.path] - reserve:
                bad.add(child)
        if v.compose == "all" and v.children:
            chain = _paths_sum(list(v.children), edges, budget)
            width = math.ceil(len(v.children) / v.concurrency) * max(budget[c] for c in v.children)
            if max(chain, width) > budget[v.path]:
                bad.add(v.path)
    return bad


def _parents(plan: AdmittedPlan, path: str) -> list[str]:
    return [v.path for v in plan.vertices if path in v.children]


def _random_budgets(declared: DeclaredTree, rng: random.Random, reserve: float) -> DeclaredTree:
    """Accepting budgets with some nodes shrunk or grown, so a tree misfits somewhere or not."""
    budgets = _accepting_budgets(declared, reserve)
    factors = [1.0, 1.0, 1.0, 1.0, 0.25, 0.5, 1.5]
    return _rebudget(
        declared,
        lambda path, _node: budgets[path] * rng.choice(factors),
        lambda _path: rng.choice([1, 2, 3]),
    )


def test_shared_node_carved_at_minimum() -> None:
    reserve = 3.0
    differing = 0
    checked = 0
    for label, declared in trees.all_trees():
        if "share" not in label:
            continue
        declared = _accepted(declared, reserve)
        plan = _compile_or_skip(declared)
        if plan is None:
            continue
        got = carve(plan, BIG, reserve, 0.0)
        assert isinstance(got, dict), (label, got)
        budget = {v.path: float(v.budget_s or 0.0) for v in plan.vertices}
        for v in plan.vertices:
            parents = _parents(plan, v.path)
            if v.path == ROOT_PATH:
                assert v.path not in got
                continue
            bounds = [budget[p] - reserve for p in parents]
            assert got[v.path].budget_s == min(bounds), (label, v.path)
            if len(parents) > 1:
                checked += 1
                differing += len(set(bounds)) > 1
                # a shared node also ends by the earliest of its parents' ends less the reserve
                assert got[v.path].end_s <= min(got[p].end_s if p in got else BIG for p in parents)
    assert checked > 0 and differing > 0  # the space holds shared nodes bounded differently


def _compile_or_skip(declared: DeclaredTree) -> AdmittedPlan | None:
    return _compile(declared)


def test_child_budget_le_parent_minus_reserve_or_refused() -> None:
    accepted = refused = 0
    for label, declared in trees.all_trees():
        rng = random.Random(label)
        for _ in range(4):
            reserve = rng.choice([0.0, 2.0, 5.0])
            plan = _compile_or_skip(_random_budgets(declared, rng, reserve))
            if plan is None:
                continue
            got = carve(plan, BIG, reserve, 0.0)
            bad = _oracle_violations(plan, reserve)
            if isinstance(got, Refusal):
                refused += 1
                assert got.code == vocab.BUDGET_DOES_NOT_FIT == "admission.budget_does_not_fit"
                assert got.identifier in {ROOT_NAME if b == ROOT_PATH else b for b in bad}, (
                    label,
                    got,
                    bad,
                )
            else:
                accepted += 1
                assert not bad, (label, bad)
                budget = {v.path: float(v.budget_s or 0.0) for v in plan.vertices}
                for v in plan.vertices:
                    for child in v.children:
                        assert budget[child] <= budget[v.path] - reserve
                        assert got[child].budget_s <= budget[v.path] - reserve
    assert accepted > 100 and refused > 100


def test_root_budget_plus_release_slice_le_deadline() -> None:
    reserve = 2.0
    seen = {"ok": 0, "deadline": 0, "ceiling": 0}
    for label, declared in trees.all_trees():
        plan = _compile_or_skip(_accepted(declared, reserve))
        if plan is None:
            continue
        root = float(plan.vertices[0].budget_s or 0.0)
        assert plan.vertices[0].path == ROOT_PATH
        for release in (0.0, 10.0, 25.0):
            for deadline in (root + release - 1.0, root + release, root + release + 7.0):
                for ceiling in (None, root + release + 3.0, BIG):
                    got = carve(plan, deadline, reserve, release, deadline_ceiling_s=ceiling)
                    fits = root + release <= deadline
                    under = ceiling is None or deadline <= ceiling
                    if fits and under:
                        seen["ok"] += 1
                        assert isinstance(got, dict), (label, got)
                        for path, s in got.items():
                            assert isinstance(s, Slice)
                            assert s.end_s <= deadline - release - reserve, (label, path)
                    else:
                        seen["deadline" if not fits else "ceiling"] += 1
                        assert isinstance(got, Refusal), (label, deadline, release, ceiling)
                        assert got.code == vocab.BUDGET_DOES_NOT_FIT
                        assert got.identifier == ROOT_NAME
    assert all(seen.values())


def _variants(declared: DeclaredTree) -> list[DeclaredTree]:
    """Every tree that keeps exactly one alternative of each choice (the choice node stays)."""
    choices = [p for p, n in declared.nodes.items() if n["compose"] == "choice"]
    picks = [range(len(declared.nodes[p]["choice"]["alternatives"])) for p in choices]
    out = []
    for combo in itertools.product(*picks):
        nodes = _copy_nodes(declared)
        for path, index in zip(choices, combo, strict=True):
            alts = nodes[path]["choice"]["alternatives"]
            nodes[path]["choice"]["alternatives"] = [alts[index]]
        out.append(DeclaredTree.build(declared.root, nodes))
    return out


def test_worst_case_over_choice_alternatives() -> None:
    # a parent of 45 s with a reserve of 10 s: an undeclared choice of a 10 s and a 40 s
    # alternative needs the 40 s one's budget plus the reserve, so it misfits although the
    # 10 s alternative alone would fit
    nodes = {
        ROOT_PATH: trees.all_node("root", [trees.child_ref("c", "c")], budget=45.0),
        "c": trees.choice_node(
            "c",
            [trees.alternative("a", "c/a"), trees.alternative("b", "c/b")],
            budget=None,
        ),
        "c/a": leaf_node("a", budget=10.0),
        "c/b": leaf_node("b", budget=40.0),
    }
    both = _compile(DeclaredTree.build("root", nodes))
    assert both is not None
    refused = carve(both, BIG, 10.0, 0.0)
    assert isinstance(refused, Refusal) and refused.identifier == "c"
    assert worst_case_s(both, 10.0) == 45.0
    # with no declared budget anywhere above the alternatives the worst case is the largest
    # alternative (40) plus the choice's reserve (10) plus the root's (10)
    open_nodes = copy.deepcopy(nodes)
    open_nodes[ROOT_PATH]["budget"] = None
    open_plan = _compile(DeclaredTree.build("root", open_nodes))
    assert open_plan is not None and worst_case_s(open_plan, 10.0) == 40.0 + 10.0 + 10.0
    only_small = copy.deepcopy(nodes)
    only_small["c"]["choice"]["alternatives"] = [trees.alternative("a", "c/a")]
    small = _compile(DeclaredTree.build("root", only_small))
    assert small is not None and isinstance(carve(small, BIG, 10.0, 0.0), dict)

    checked = misfit_only_worst = 0
    for label, declared in trees.all_trees():
        if "share" in label or not any(n["compose"] == "choice" for n in declared.nodes.values()):
            continue
        rng = random.Random(label)
        pool = [None, 1.0, 5.0, 10.0, 30.0, 60.0]
        for _ in range(30):
            reserve = rng.choice([0.0, 2.0, 5.0])
            drawn = _rebudget(declared, _draw(rng, pool), _draw_int(rng, (1, 2, 3)))
            full = _compile(drawn)
            if full is None:
                continue
            variants = [_compile(v) for v in _variants(drawn)]
            assert all(v is not None for v in variants)
            verdicts = [isinstance(carve(v, BIG, reserve, 0.0), Refusal) for v in variants if v]
            got = carve(full, BIG, reserve, 0.0)
            assert isinstance(got, Refusal) == any(verdicts), (label, reserve)
            assert worst_case_s(full, reserve) == max(
                worst_case_s(v, reserve) for v in variants if v
            ), label
            checked += 1
            misfit_only_worst += isinstance(got, Refusal) and not all(verdicts)
    assert checked > 100
    assert misfit_only_worst > 0  # some tree fits under one alternative and misfits under another


def _with_effects(declared: DeclaredTree, rng: random.Random) -> DeclaredTree:
    """The same tree with a random subset of its leaves given CREATE+RUN effects."""
    nodes = _copy_nodes(declared)
    for node in nodes.values():
        if node["compose"] == "leaf" and rng.random() < 0.6:
            node["effects"] = [
                create_run_effect(f"e{n}", release_timeout=rng.choice([1.0, 5.0, 7.0, 20.0]))
                for n in range(rng.choice([1, 2]))
            ]
    return DeclaredTree.build(declared.root, nodes)


def test_no_release_walk_release_slice_zero() -> None:
    published = 25.0
    walked = unwalked = 0
    for label, declared in trees.all_trees():
        declared = _accepted(declared, 1.0)
        plan = _compile_or_skip(declared)
        if plan is None:
            continue
        assert not any(v.create_run for v in plan.vertices)
        assert release_slice_for(plan, published) == 0.0, label
        unwalked += 1
        # a root with a release walk keeps the published slice
        with_walk = _compile_or_skip(_with_effects(declared, random.Random(label)))
        if with_walk is not None and any(v.create_run for v in with_walk.vertices):
            assert release_slice_for(with_walk, published) == published, label
            walked += 1
        # carved with slice 0, the root's release point is its admitted deadline
        got = carve(plan, BIG, 1.0, release_slice_for(plan, published))
        assert isinstance(got, dict)
        assert all(s.end_s <= BIG for s in got.values())
    assert walked > 50 and unwalked > 100
    # a plain plugin: the implicit depth-1 plan has one vertex, no carve, no release slice
    implicit = implicit_depth1_plan()
    assert release_slice_for(implicit, published) == 0.0
    assert carve(implicit, BIG, 1.0, 0.0) == {}
    # a single-leaf root with a walk: budget + slice must fit; with none, budget alone
    walk_leaf = DeclaredTree.build(
        "root",
        {ROOT_PATH: leaf_node("root", budget=30.0, effects=[create_run_effect("c")])},
    )
    no_walk_leaf = DeclaredTree.build("root", {ROOT_PATH: leaf_node("root", budget=30.0)})
    walk_plan, plain_plan = _compile(walk_leaf), _compile(no_walk_leaf)
    assert walk_plan is not None and plain_plan is not None
    assert isinstance(carve(walk_plan, 30.0, 1.0, release_slice_for(walk_plan, published)), Refusal)
    assert carve(plain_plan, 30.0, 1.0, release_slice_for(plain_plan, published)) == {}
    # the carve attaches to the plan and digests: the slice and the release slice are covered
    plan = _compile_or_skip(_accepted(trees.build(("all", (("leaf",), ("leaf",)))), 1.0))
    assert plan is not None
    slices = carve(plan, BIG, 1.0, 0.0)
    assert isinstance(slices, dict)
    carved = attach(plan, slices, 0.0)
    assert carved.release_slice == 0.0 and set(carved.slices) == set(slices)
    assert AdmittedPlan.from_json(carved.to_json()) == carved
    assert carved.plan_digest != plan.plan_digest


def _expected_margin(
    declared: DeclaredTree, plan: AdmittedPlan, grace: float, kill: float, parallelism: int
) -> float:
    """B2-C2 (5) written from the declaration: group each CREATE+RUN effect's release timeout by
    its node's release rank."""
    by_rank: dict[int, list[float]] = {}
    for path, node in declared.nodes.items():
        if path not in plan.release_rank or node["compose"] != "leaf":
            continue
        for effect in node["effects"]:
            if effect["facet"] == "create" and effect["lifetime"] == "run":
                by_rank.setdefault(plan.release_rank[path], []).append(effect["release_timeout"])
    total = grace + kill
    for timeouts in by_rank.values():
        total += math.ceil(len(timeouts) / parallelism) * 5 * max(timeouts)
    return total


def test_margin_needed_is_b2c2_5_sum() -> None:
    grace, kill = tolerances.grace(), tolerances.kill()
    assert SWEEP_STEPS == 5  # observe, stop, remove, observe, settle
    # no target: the plan-less form, grace + kill
    bare = compile(trees.build(("all", (("leaf",), ("leaf",)))), {})
    assert isinstance(bare, AdmittedPlan)
    assert margin_needed(bare, MarginLimits(grace, kill, 1)) == grace + kill
    assert margin_needed(implicit_depth1_plan(), MarginLimits(grace, kill, 4)) == grace + kill
    # two targets in one rank, timeouts 5 and 7: parallelism 1 sweeps them in turn, 2 together
    pair = _compile(
        DeclaredTree.build(
            "root",
            {
                ROOT_PATH: trees.all_node(
                    "root",
                    [trees.child_ref("a", "a"), trees.child_ref("b", "b")],
                ),
                "a": leaf_node("a", effects=[create_run_effect("x", release_timeout=5.0)]),
                "b": leaf_node("b", effects=[create_run_effect("y", release_timeout=7.0)]),
            },
        )
    )
    assert pair is not None and set(pair.release_rank.values()) == {0}
    assert margin_needed(pair, MarginLimits(grace, kill, 1)) == grace + kill + 2 * 5 * 7
    assert margin_needed(pair, MarginLimits(grace, kill, 2)) == grace + kill + 1 * 5 * 7
    # a dependency ranks above its dependent: two ranks, each one target
    chained = _compile(
        DeclaredTree.build(
            "root",
            {
                ROOT_PATH: trees.all_node(
                    "root",
                    [trees.child_ref("a", "a"), trees.child_ref("b", "b", needs=["a"])],
                ),
                "a": leaf_node("a", effects=[create_run_effect("x", release_timeout=5.0)]),
                "b": leaf_node("b", effects=[create_run_effect("y", release_timeout=7.0)]),
            },
        )
    )
    assert chained is not None and len(set(chained.release_rank.values())) == 2
    assert margin_needed(chained, MarginLimits(grace, kill, 3)) == grace + kill + 5 * 5 + 5 * 7
    # generated plans
    counted = 0
    for label, declared in trees.all_trees():
        rng = random.Random(label)
        with_effects = _with_effects(declared, rng)
        plan = _compile_or_skip(with_effects)
        if plan is None:
            continue
        for parallelism in (1, 2, 3):
            limits = MarginLimits(grace, kill, parallelism)
            assert margin_needed(plan, limits) == _expected_margin(
                with_effects, plan, grace, kill, parallelism
            ), (label, parallelism)
        counted += any(v.create_run for v in plan.vertices)
    assert counted > 50
