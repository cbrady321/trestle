"""Property suite for the precedence ordinal, release rank, not-started closure, plan digest and
format version (MC-20, MC-22's ordinal property; L.SV-3.3): shape evidence only."""

from __future__ import annotations

import copy
import json
import random
from typing import Any

import pytest

from tests.proof.foundations import trees
from tests.proof.foundations.trees import all_node, child_ref, leaf_node
from trestle.common.plan import formats, ordinal
from trestle.common.plan.compiler import (
    AdmittedPlan,
    Refusal,
    compile,
    implicit_depth1_plan,
    plan_shape_equal,
)
from trestle.common.plan.declared import ROOT_PATH, DeclaredTree, canonical_json


def _plans() -> list[tuple[str, DeclaredTree, AdmittedPlan]]:
    out = []
    for label, declared in trees.all_trees():
        got = compile(declared, {})
        if isinstance(got, AdmittedPlan):
            out.append((label, declared, got))
    return out


def _kids(plan: AdmittedPlan) -> dict[str, tuple[str, ...]]:
    return {v.path: v.children for v in plan.vertices}


def _expanded_preds(plan: AdmittedPlan) -> dict[str, set[str]]:
    """Brute-force: every vertex of a dependency's subtree precedes every vertex of the
    dependent's subtree."""
    kids = _kids(plan)

    def sub(path: str) -> set[str]:
        out = {path}
        for kid in kids[path]:
            out |= sub(kid)
        return out

    preds: dict[str, set[str]] = {p: set() for p in kids}
    for dependency, dependent in plan.edges:
        for v in sub(dependent):
            preds[v] |= sub(dependency)
    return preds


def test_ordinal_total_unique() -> None:
    for label, _, plan in _plans():
        assert sorted(plan.precedence_ordinal.values()) == list(range(len(plan.vertices))), label
        assert set(plan.precedence_ordinal) == set(plan.paths), label


def test_descendant_before_composite() -> None:
    """On an equal leaf key a descendant ranks before its composite, and a composite ranks before
    every vertex of its subtree with a greater leaf key (design S-8)."""
    checked = 0
    for label, _, plan in _plans():
        kids = _kids(plan)
        # leaf-ness by compose, not by absence of children (an empty composite is not a leaf)
        compose = {v.path: v.compose for v in plan.vertices}
        walk = ordinal.place(ROOT_PATH, lambda p, k=kids: k[p], lambda p, c=compose: c[p] == "leaf")
        o = plan.precedence_ordinal

        def canonical_subtree(path: str, w: ordinal.Placement = walk, k: Any = kids) -> set[str]:
            out: set[str] = set()
            stack = [path]
            while stack:
                cur = stack.pop()
                for kid in k[cur]:
                    if kid not in out and w.order.index(kid) > w.order.index(path):
                        out.add(kid)
                        stack.append(kid)
            return out

        for path, kind in compose.items():
            if kind == "leaf":
                continue
            for d in canonical_subtree(path):
                if walk.entry[d] == walk.entry[path] and walk.post[d] < walk.post[path]:
                    assert o[d] < o[path], (label, d, path)
                    checked += 1
                elif walk.entry[d] > walk.entry[path]:
                    assert o[path] < o[d], (label, d, path)
                    checked += 1
    assert checked > 100


def _wrap(declared: DeclaredTree, target: str) -> tuple[DeclaredTree, dict[str, str]] | None:
    """Wrap the node at `target` in a new composite that takes its declaration position, name and
    edges; the node moves one level down. Returns the tree and the old-path -> new-path map."""
    nodes = copy.deepcopy(dict(declared.nodes))
    holders = [
        (path, ref)
        for path, node in nodes.items()
        if node["compose"] == "all"
        for ref in node["children"]
        if ref["path"] == target
    ]
    if not holders or target == ROOT_PATH:
        return None
    inner = f"{target}/wrapped"

    def moved(path: str) -> str:
        if path == target or path.startswith(f"{target}/"):
            return inner + path[len(target) :]
        return path

    mapping = {p: moved(p) for p in nodes}
    remapped: dict[str, Any] = {}
    for path, node in nodes.items():
        for ref in node.get("children", ()):
            if ref["path"] is not None:
                ref["path"] = moved(ref["path"])
        for alt in node.get("choice", {}).get("alternatives", ()):
            if alt["path"] is not None:
                alt["path"] = moved(alt["path"])
        remapped[moved(path)] = node
    first_ref = holders[0][1]
    wrapper_ref = child_ref(first_ref["name"], inner, unit=first_ref["binding"]["unit"])
    remapped[target] = all_node("wrap", [wrapper_ref])
    for _, ref in holders:
        ref["path"] = target  # every reference to the node now reaches the wrapper
    # the wrapper's own ref carries no needs; the outer refs keep theirs
    return DeclaredTree.build(declared.root, remapped), mapping


def test_wrapping_preserves_relative_order() -> None:
    checked = 0
    for label, declared, plan in _plans():
        if "share" in label:
            continue
        for target in plan.paths:
            wrapped = _wrap(declared, target)
            if wrapped is None:
                continue
            new_tree, mapping = wrapped
            got = compile(new_tree, {})
            assert isinstance(got, AdmittedPlan), (label, target, got)
            old = sorted(plan.paths, key=lambda p: plan.precedence_ordinal[p])
            new = [
                p
                for p in sorted(got.paths, key=lambda p: got.precedence_ordinal[p])
                if p in {mapping[o] for o in old}
            ]
            assert new == [mapping[o] for o in old], (label, target)
            checked += 1
    assert checked > 100


def test_release_rank_descending_dependency() -> None:
    for label, _, plan in _plans():
        preds = _expanded_preds(plan)
        for v, before in preds.items():
            expected = 1 + max((plan.release_rank[u] for u in before), default=-1)
            assert plan.release_rank[v] == expected, (label, v)
        # descending rank releases every dependent before what it depends on
        order = sorted(plan.paths, key=lambda p: -plan.release_rank[p])
        for v, before in preds.items():
            for u in before:
                assert order.index(v) < order.index(u), (label, v, u)
    leaf = compile(trees.tree("root", {ROOT_PATH: leaf_node("root")}), {})
    assert isinstance(leaf, AdmittedPlan) and dict(leaf.release_rank) == {ROOT_PATH: 0}


def test_not_started_closure_dependents_only() -> None:
    checked = 0
    for label, _, plan in _plans():
        kids = _kids(plan)
        preds = _expanded_preds(plan)
        dependents = {v: {w for w, before in preds.items() if v in before} for v in plan.paths}

        def transitive(v: str, deps: dict[str, set[str]] = dependents) -> set[str]:
            out: set[str] = set()
            stack = [v]
            while stack:
                for nxt in deps[stack.pop()]:
                    if nxt not in out:
                        out.add(nxt)
                        stack.append(nxt)
            return out

        for seed in plan.paths:
            closure = ordinal.not_started_closure(ROOT_PATH, kids, plan.edges, [seed])
            assert closure == transitive(seed) - {seed}, (label, seed)
            assert seed not in closure
            assert not closure & preds[seed], (label, seed)  # never a dependency
            checked += 1
    assert checked > 100
    # explicit: a blocked db stops its dependents and their subtrees, not siblings or the parent
    nodes: dict[str, Any] = {n: leaf_node(n) for n in ("db", "api", "web", "x", "y")}
    nodes["grp"] = all_node("grp", [child_ref("x", "x"), child_ref("y", "y")])
    nodes[ROOT_PATH] = all_node(
        "root",
        [
            child_ref("db", "db"),
            child_ref("grp", "grp", needs=["db"]),
            child_ref("api", "api", needs=["db"]),
            child_ref("web", "web"),
        ],
    )
    plan = compile(trees.tree("root", nodes), {})
    assert isinstance(plan, AdmittedPlan)
    got = ordinal.not_started_closure(ROOT_PATH, _kids(plan), plan.edges, ["db"])
    assert got == {"grp", "x", "y", "api"}
    assert ordinal.not_started_closure(ROOT_PATH, _kids(plan), plan.edges, ["web"]) == frozenset()
    assert ordinal.not_started_closure(ROOT_PATH, _kids(plan), plan.edges, ["x"]) == frozenset()


def test_digest_stable_format_versioned() -> None:
    for label, declared, plan in _plans():
        again = compile(declared, {})
        assert isinstance(again, AdmittedPlan) and again.plan_digest == plan.plan_digest, label
        text = plan.to_json()
        assert AdmittedPlan.from_json(text) == plan, label
        assert AdmittedPlan.from_json(text).to_json() == text, label
        assert canonical_json(json.loads(text)) == text  # already canonical
        keys = list(declared.nodes)
        random.Random(label).shuffle(keys)
        shuffled = DeclaredTree.build(declared.root, {k: declared.nodes[k] for k in keys})
        other = compile(shuffled, {})
        assert isinstance(other, AdmittedPlan) and other.plan_digest == plan.plan_digest, label
    plans = _plans()
    assert len({p.plan_digest for _, _, p in plans}) > 50  # the digest separates plans
    # the format version is hashed: the same body under another version is another digest
    body = plans[0][2].body()
    assert formats.plan_digest({**body, "format_version": 2}) != formats.plan_digest(body)
    assert plans[0][2].format_version == formats.PLAN_FORMAT == 1
    # attaching slices changes the digest (it covers every field)
    plan = plans[-1][2]
    carved = AdmittedPlan.from_json(plan.to_json())
    assert carved.plan_digest == plan.plan_digest
    assert formats.plan_digest({**plan.body(), "slices": {ROOT_PATH: 5.0}}) != plan.plan_digest


def test_declaration_digest_equals_declared_tree_digest() -> None:
    for label, declared, plan in _plans():
        assert plan.declaration_digest == declared.digest, label
        assert AdmittedPlan.from_json(plan.to_json()).declaration_digest == declared.digest
        assert declared.digest != plan.plan_digest  # two digests, side by side
    assert implicit_depth1_plan().declaration_digest is None


def test_implicit_depth1_shape_equals_compiled_leaf_root() -> None:
    compiled = compile(trees.tree("root", {ROOT_PATH: leaf_node("root")}), {})
    assert isinstance(compiled, AdmittedPlan)
    implicit = implicit_depth1_plan()
    assert plan_shape_equal(implicit, compiled) and plan_shape_equal(compiled, implicit)
    assert implicit.plan_digest is None and implicit.declaration_digest is None
    assert implicit.release_slice == 0.0 and dict(implicit.release_rank) == {ROOT_PATH: 0}
    assert dict(implicit.precedence_ordinal) == {ROOT_PATH: 0} and len(implicit.vertices) == 1
    # metadata is excluded: digests and format version differ, shape equality holds
    assert implicit.plan_digest != compiled.plan_digest
    # ...but a real difference in shape is seen
    two = compile(
        trees.tree(
            "root",
            {"a": leaf_node("a"), ROOT_PATH: all_node("root", [child_ref("a", "a")])},
        ),
        {},
    )
    assert isinstance(two, AdmittedPlan) and not plan_shape_equal(implicit, two)
    # the implicit plan round-trips without a digest
    assert AdmittedPlan.from_json(implicit.to_json()) == implicit


def test_unknown_format_raises_typed() -> None:
    plan = _plans()[0][2]
    document = json.loads(plan.to_json())
    document["format_version"] = 2
    with pytest.raises(formats.UnknownPlanFormat):
        AdmittedPlan.from_json(canonical_json(document))
    with pytest.raises(formats.UnknownPlanFormat):
        AdmittedPlan.from_json(json.dumps({**document, "format_version": True}))
    for text in ("not json", "[]"):
        with pytest.raises(formats.PlanInvalid):
            AdmittedPlan.from_json(text)
    tampered = json.loads(plan.to_json())
    tampered["release_slice"] = 9.0
    with pytest.raises(formats.PlanInvalid):
        AdmittedPlan.from_json(canonical_json(tampered))
    missing = json.loads(plan.to_json())
    del missing["plan_digest"]
    with pytest.raises(formats.PlanInvalid):
        AdmittedPlan.from_json(canonical_json(missing))
    assert issubclass(formats.UnknownPlanFormat, ValueError)


def test_refusals_carry_no_plan() -> None:
    nodes: dict[str, Any] = {"a": leaf_node("a"), "b": leaf_node("b")}
    nodes[ROOT_PATH] = all_node(
        "root", [child_ref("a", "a", needs=["b"]), child_ref("b", "b", needs=["a"])]
    )
    assert isinstance(compile(trees.tree("root", nodes), {}), Refusal)
