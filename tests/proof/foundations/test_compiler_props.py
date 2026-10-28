"""Property suite for the pure plan compiler (MC-23; L.SV-3.1): shape evidence only."""

from __future__ import annotations

import copy
import hashlib
import random
from typing import Any

from tests.proof.foundations import trees
from tests.proof.foundations.trees import (
    all_node,
    alternative,
    child_ref,
    choice_node,
    create_run_effect,
    leaf_node,
)
from trestle.common.plan import vocabulary as vocab
from trestle.common.plan.compiler import (
    VERTEX_MAX,
    AdmittedPlan,
    CompileLimits,
    Refusal,
    compile,
)
from trestle.common.plan.declared import ROOT_PATH, DeclaredTree, canonical_json


def _flat(names: list[str], **root: Any) -> DeclaredTree:
    nodes: dict[str, Any] = {n: leaf_node(n) for n in names}
    nodes[ROOT_PATH] = all_node("root", [child_ref(n, n) for n in names], **root)
    return trees.tree("root", nodes)


def _plan(declared: DeclaredTree, request: dict[str, Any] | None = None) -> AdmittedPlan:
    got = compile(declared, request or {})
    assert isinstance(got, AdmittedPlan), got
    return got


def _refusal(declared: DeclaredTree, request: dict[str, Any] | None = None) -> Refusal:
    got = compile(declared, request or {})
    assert isinstance(got, Refusal), got
    return got


def _check_plan(plan: AdmittedPlan) -> None:
    paths = plan.paths
    assert paths[0] == ROOT_PATH and len(set(paths)) == len(paths)
    scope = set(paths)
    for dependency, dependent in plan.edges:
        assert dependency in scope and dependent in scope
        assert plan.release_rank[dependent] > plan.release_rank[dependency]
    assert sorted(plan.precedence_ordinal.values()) == list(range(len(paths)))
    assert set(plan.release_rank) == set(plan.precedence_ordinal) == scope
    for vertex in plan.vertices:
        assert set(vertex.children) <= scope and set(vertex.needs) <= scope
    assert plan.plan_digest == hashlib.sha256(canonical_json(plan.body()).encode()).hexdigest()


def test_exhaustive_trees_compile_or_refuse_total() -> None:
    count = 0
    for label, declared in trees.all_trees():
        first = compile(declared, {})
        assert isinstance(first, (AdmittedPlan, Refusal)), label
        assert compile(declared, {}) == first, label  # deterministic
        if isinstance(first, AdmittedPlan):
            _check_plan(first)
            assert first.declaration_digest == declared.digest
        else:
            assert first.code in vocab.PLAN_REFUSAL_CODES and first.identifier, label
        count += 1
    assert count > 100


def test_generator_is_not_vacuous_and_refusals_are_the_shared_needs_cycles() -> None:
    """Only a needs edge between two subtrees that share a leaf is a cycle; everything else
    compiles."""
    accepted = []
    for label, declared in trees.all_trees():
        got = compile(declared, {})
        if isinstance(got, AdmittedPlan):
            accepted.append(got)
        else:
            assert got.code == vocab.DEPENDENCY_CYCLE and "-n1-share" in label, (label, got)
    assert len(accepted) > 100
    assert any(len(p.vertices) >= 5 for p in accepted)  # depth 3, fan-out 2
    assert any(p.edges for p in accepted)


def test_permuted_declaration_order_same_digest() -> None:
    for label, declared in trees.all_trees():
        base = compile(declared, {})
        if not isinstance(base, AdmittedPlan):
            continue
        keys = list(declared.nodes)
        random.Random(label).shuffle(keys)
        shuffled = DeclaredTree.build(declared.root, {k: declared.nodes[k] for k in keys})
        assert shuffled.digest == declared.digest, label
        again = compile(shuffled, {})
        assert isinstance(again, AdmittedPlan) and again == base, label
    # the request's argument order and the order of a list of identifiers change nothing
    names = ["a", "b", "c"]
    declared = _flat(
        names,
        identifier_sets={"svcs": names},
        arg_bindings=[{"arg": "services", "identifier_set": "svcs", "filters_children": True}],
    )
    forward = _plan(declared, {"services": ["a", "c"], "other": 1})
    backward = _plan(declared, {"other": 1, "services": ["c", "a"]})
    assert forward == backward and forward.plan_digest == backward.plan_digest


def test_vertex_max_refused_bound_exceeded() -> None:
    fits = _flat([f"n{i}" for i in range(VERTEX_MAX - 1)])
    assert len(_plan(fits).vertices) == VERTEX_MAX
    over = _flat([f"n{i}" for i in range(VERTEX_MAX)])
    refused = _refusal(over)
    assert refused.code == vocab.BOUND_EXCEEDED and refused.identifier == "selected_scope"
    limited = compile(_flat(["a", "b", "c"]), {}, CompileLimits(vertex_max=3))
    assert isinstance(limited, Refusal) and limited.code == vocab.BOUND_EXCEEDED
    ok = compile(_flat(["a", "b"]), {}, CompileLimits(vertex_max=3))
    assert isinstance(ok, AdmittedPlan)


def test_bound_check_precedes_identifier_check() -> None:
    """B2-C2's order: (0) fires before (1) when both would."""
    names = [f"n{i}" for i in range(6)]
    declared = _flat(
        names,
        identifier_sets={"svcs": names},
        arg_bindings=[{"arg": "services", "identifier_set": "svcs", "filters_children": False}],
    )
    got = compile(declared, {"services": ["nope"]}, CompileLimits(vertex_max=3))
    assert isinstance(got, Refusal) and got.code == vocab.BOUND_EXCEEDED


def test_unknown_identifier_names_value_and_locator() -> None:
    names = ["a", "b", "c"]
    declared = _flat(
        names,
        identifier_sets={"svcs": names},
        arg_bindings=[{"arg": "services", "identifier_set": "svcs", "filters_children": True}],
    )
    refused = _refusal(declared, {"services": ["a", "zz"]})
    assert refused.code == vocab.UNKNOWN_IDENTIFIER
    assert refused.identifier == "zz"
    assert refused.valid_listed_at is not None and "identifier_sets.svcs" in refused.valid_listed_at
    assert _refusal(declared, {"services": "zz"}).identifier == "zz"
    assert _refusal(declared, {"services": [7]}).code == vocab.UNKNOWN_IDENTIFIER
    # valid identifiers filter the scope; no value means no filtering
    assert _plan(declared, {"services": ["b"]}).paths == (ROOT_PATH, "b")
    assert len(_plan(declared, {}).vertices) == 4
    # a request value at a nested dotted path is found
    nested = _flat(
        names,
        identifier_sets={"svcs": names},
        arg_bindings=[{"arg": "opts.services", "identifier_set": "svcs", "filters_children": True}],
    )
    assert _refusal(nested, {"opts": {"services": ["q"]}}).identifier == "q"


def test_filtered_scope_includes_needs_closure() -> None:
    names = ["db", "cache", "api", "web"]
    nodes: dict[str, Any] = {n: leaf_node(n) for n in names}
    nodes[ROOT_PATH] = all_node(
        "root",
        [
            child_ref("db", "db"),
            child_ref("cache", "cache", needs=["db"]),
            child_ref("api", "api", needs=["cache"]),
            child_ref("web", "web"),
        ],
        identifier_sets={"svcs": names},
        arg_bindings=[{"arg": "services", "identifier_set": "svcs", "filters_children": True}],
    )
    declared = trees.tree("root", nodes)
    plan = _plan(declared, {"services": ["api"]})
    assert set(plan.paths) == {ROOT_PATH, "api", "cache", "db"}
    assert plan.edges == (("cache", "api"), ("db", "cache"))
    assert plan.release_rank["api"] > plan.release_rank["cache"] > plan.release_rank["db"] == 0
    assert plan.vertex("api").needs == ("cache",)


def test_cycle_refused_names_node() -> None:
    nodes: dict[str, Any] = {"a": leaf_node("a"), "b": leaf_node("b"), "c": leaf_node("c")}
    nodes[ROOT_PATH] = all_node(
        "root",
        [child_ref("a", "a", needs=["b"]), child_ref("b", "b", needs=["a"]), child_ref("c", "c")],
    )
    refused = _refusal(trees.tree("root", nodes))
    assert refused.code == vocab.DEPENDENCY_CYCLE and refused.identifier in {"a", "b"}
    # a containment cycle: a composite that lists its own ancestor
    loop_nodes: dict[str, Any] = {
        "x": all_node("x", [child_ref("root", ROOT_PATH)]),
        ROOT_PATH: all_node("root", [child_ref("x", "x")]),
    }
    contained = _refusal(trees.tree("root", loop_nodes))
    assert contained.code == vocab.DEPENDENCY_CYCLE
    # a cycle that only exists once a shared leaf is placed under both sides of a needs edge
    shared: dict[str, Any] = {
        "p": all_node("p", [child_ref("leaf", "leaf")]),
        "q": all_node("q", [child_ref("leaf", "leaf")]),
        "leaf": leaf_node("leaf"),
        ROOT_PATH: all_node("root", [child_ref("p", "p"), child_ref("q", "q", needs=["p"])]),
    }
    expanded = _refusal(trees.tree("root", shared))
    assert expanded.code == vocab.DEPENDENCY_CYCLE and expanded.identifier == "leaf"


def test_unit_unresolved_refusals_name_the_missing_element() -> None:
    nodes: dict[str, Any] = {"a": leaf_node("a")}
    nodes[ROOT_PATH] = all_node("root", [child_ref("a", "a"), child_ref("b", None)])
    unresolved = _refusal(trees.tree("root", nodes))
    assert unresolved.code == vocab.UNIT_UNRESOLVED and unresolved.identifier.endswith("/b")
    nodes[ROOT_PATH] = all_node("root", [child_ref("a", "a", needs=["ghost"])])
    ghost = _refusal(trees.tree("root", nodes))
    assert ghost.code == vocab.UNIT_UNRESOLVED and ghost.identifier.endswith("/ghost")
    nodes[ROOT_PATH] = all_node("root", [child_ref("a", "a")], gates=["ghost"])
    assert _refusal(trees.tree("root", nodes)).code == vocab.UNIT_UNRESOLVED
    fallback = {
        "x": leaf_node("x"),
        ROOT_PATH: choice_node("root", [alternative("x", "x")], fallback="y"),
    }
    assert _refusal(trees.tree("root", fallback)).code == vocab.UNIT_UNRESOLVED


def _shared_leaf(p1: dict[str, Any], p2: dict[str, Any]) -> DeclaredTree:
    nodes: dict[str, Any] = {
        "s": leaf_node("s"),
        "left": all_node("left", [child_ref("s", "s", params=p1)]),
        "right": all_node("right", [child_ref("s", "s", params=p2)]),
        ROOT_PATH: all_node("root", [child_ref("left", "left"), child_ref("right", "right")]),
    }
    return trees.tree("root", nodes)


def test_param_conflict_refused_declaration_conflict() -> None:
    conflicting = _shared_leaf({"port": 80}, {"port": 81})
    refused = _refusal(conflicting)
    assert refused.code == vocab.DECLARATION_CONFLICT and refused.identifier == "s"
    # a conflict that only appears once argument references are resolved against the request
    by_arg = _shared_leaf({"port": "cfg.port"}, {"port": 8080})
    assert isinstance(compile(by_arg, {"cfg": {"port": 8080}}), AdmittedPlan)
    got = _refusal(by_arg, {"cfg": {"port": 9}})
    assert got.code == vocab.DECLARATION_CONFLICT and got.identifier == "s"
    # the same name bound to two nodes of one composite is a conflict too
    twice: dict[str, Any] = {
        "a1": leaf_node("a"),
        "a2": leaf_node("a"),
        ROOT_PATH: all_node("root", [child_ref("a", "a1"), child_ref("a", "a2")]),
    }
    assert _refusal(trees.tree("root", twice)).code == vocab.DECLARATION_CONFLICT


def test_equal_params_one_vertex() -> None:
    plan = _plan(_shared_leaf({"port": 80}, {"port": 80}))
    assert plan.paths.count("s") == 1
    assert sorted(plan.precedence_ordinal.values()) == list(range(len(plan.paths)))
    assert plan.vertex("left").children == plan.vertex("right").children == ("s",)


def test_leaf_root_depth1() -> None:
    declared = trees.tree("root", {ROOT_PATH: leaf_node("root")})
    plan = _plan(declared)
    assert len(plan.vertices) == 1 and plan.paths == (ROOT_PATH,)
    assert plan.release_rank == {ROOT_PATH: 0}
    assert plan.precedence_ordinal == {ROOT_PATH: 0}
    assert plan.edges == () and plan.lease_set == () and plan.slices == {}
    assert plan.declaration_digest == declared.digest and plan.format_version == 1


def test_multi_vertex_tree_compiles() -> None:
    plan = _plan(_flat(["a", "b"]))
    assert plan.paths == (ROOT_PATH, "a", "b")
    # a composite takes its first placed leaf's position, after every vertex of its subtree that
    # shares it and before the subtree's later leaves (design S-8)
    assert plan.precedence_ordinal == {"a": 0, ROOT_PATH: 1, "b": 2}


def test_ordinals_place_a_composite_at_its_first_leaf() -> None:
    plan = _plan(trees.build(("all", (("all", (trees.LEAF, trees.LEAF)), trees.LEAF))))
    o = plan.precedence_ordinal
    assert o["n1/n3"] < o["n1"] < o[ROOT_PATH] < o["n1/n4"] < o["n2"]


def test_choice_eligibility_and_selection_arg() -> None:
    nodes: dict[str, Any] = {
        "pg": leaf_node("pg"),
        "pgl": leaf_node("pgl"),
        ROOT_PATH: choice_node(
            "root", [alternative("pg", "pg"), alternative("pgl", "pgl")], select_arg="backend"
        ),
    }
    declared = trees.tree("root", nodes)
    assert _plan(declared).eligible == {ROOT_PATH: ("pg", "pgl")}
    assert _plan(declared, {"backend": ["pgl"]}).eligible == {ROOT_PATH: ("pgl",)}
    refused = _refusal(declared, {"backend": ["nothing"]})
    assert refused.code == vocab.ROUTE_UNSUPPORTED and refused.identifier == "<root>"


def _routed(select_arg: str | None) -> DeclaredTree:
    nodes: dict[str, Any] = {
        "pg": leaf_node("pg"),
        "pgl": leaf_node("pgl"),
        "db": choice_node(
            "db",
            [
                alternative("pg", "pg", reachable_from=("container", "host")),
                alternative("pgl", "pgl", reachable_from=("host",)),
            ],
            select_arg=select_arg,
        ),
        "gw": leaf_node("gw"),
        ROOT_PATH: all_node(
            "root",
            [child_ref("db", "db"), child_ref("gw", "gw", needs=["db"], vantage="container")],
        ),
    }
    return trees.tree("root", nodes)


def test_route_unsupported_when_no_eligible_alternative_is_reachable() -> None:
    declared = _routed("backend")
    assert isinstance(compile(declared, {}), AdmittedPlan)  # the container-reachable one exists
    forced = _refusal(declared, {"backend": ["pgl"]})
    assert forced.code == vocab.ROUTE_UNSUPPORTED and forced.identifier.endswith("gw")
    assert isinstance(compile(declared, {"backend": ["pg"]}), AdmittedPlan)


def test_lease_set_undecidable() -> None:
    nodes: dict[str, Any] = {
        "svc": leaf_node("svc", env_key_field="compose.project"),
        ROOT_PATH: all_node("root", [child_ref("svc", "svc")], env_key_field="compose_project"),
    }
    declared = trees.tree("root", nodes)
    missing = _refusal(declared, {})
    assert missing.code == vocab.LEASE_SET_UNDECIDABLE and missing.identifier == "compose_project"
    differs = _refusal(declared, {"compose_project": "a", "compose": {"project": "b"}})
    assert differs.code == vocab.LEASE_SET_UNDECIDABLE and differs.identifier == "svc"
    same = _plan(declared, {"compose_project": "a", "compose": {"project": "a"}})
    assert same.lease_set == (canonical_json("a"),)
    # a root that declares no environment is admitted with no lease, whatever a node says
    free = trees.tree(
        "root",
        {
            "svc": leaf_node("svc", env_key_field="compose.project"),
            ROOT_PATH: all_node("root", [child_ref("svc", "svc")]),
        },
    )
    assert _plan(free, {}).lease_set == ()


def test_checks_fire_in_b2c2_order() -> None:
    """Integrity first, then (0) bound, (1) identifier, (2)/(3) route, (6) lease, (7) conflict."""
    nodes: dict[str, Any] = {
        "s": leaf_node("s"),
        "left": all_node("left", [child_ref("s", "s", params={"p": 1})]),
        "right": all_node("right", [child_ref("s", "s", params={"p": 2})]),
        ROOT_PATH: all_node(
            "root",
            [child_ref("left", "left"), child_ref("right", "right")],
            identifier_sets={"ids": ["left"]},
            arg_bindings=[{"arg": "who", "identifier_set": "ids", "filters_children": False}],
            env_key_field="env",
        ),
    }
    declared = trees.tree("root", nodes)
    assert _refusal(declared, {"who": ["x"]}).code == vocab.UNKNOWN_IDENTIFIER  # (1) < (6), (7)
    assert _refusal(declared, {"who": ["left"]}).code == vocab.LEASE_SET_UNDECIDABLE  # (6) < (7)
    assert _refusal(declared, {"who": ["left"], "env": "e"}).code == vocab.DECLARATION_CONFLICT


def test_create_run_effects_are_recorded_as_sweep_targets() -> None:
    effects = [create_run_effect("make", 7.0), dict(create_run_effect("x"), lifetime="durable")]
    declared = trees.tree("root", {ROOT_PATH: leaf_node("root", effects=effects)})
    assert _plan(declared).vertex(ROOT_PATH).create_run == (("make", 7.0),)


def test_compile_reads_nothing_but_its_arguments() -> None:
    declared = _flat(["a", "b"])
    request = {"x": {"y": 1}}
    before = copy.deepcopy(request)
    first, second = compile(declared, request), compile(declared, request)
    assert first == second and request == before
