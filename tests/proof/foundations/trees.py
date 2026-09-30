"""Stdlib tree generator for the foundations property suites (L.SV-3.1).

`all_trees()` enumerates every declared tree of depth <= 3 and fan-out <= 2 with at most one
shared node (a leaf referenced from two places), each with and without one needs edge between the
root's first two children. Shapes are nested tuples; `build` turns one into a `DeclaredTree`
whose nodes are keyed by canonical path (V-1.2; first depth-first occurrence). The builders
(`leaf_node`, `all_node`, `choice_node`) are the wire shape of `trestle.common.plan.declared`,
so a suite can also hand-write a tree.
"""

from __future__ import annotations

import copy
from collections.abc import Iterator, Mapping, Sequence
from typing import Any

from trestle.common.plan.declared import ROOT_PATH, DeclaredTree

Shape = tuple[Any, ...]  # ("leaf",) | ("all", (shape, ...)) | ("choice", n_alternatives)

LEAF: Shape = ("leaf",)


def leaf_node(
    unit: str,
    *,
    budget: float | None = 10.0,
    env_key_field: str | None = None,
    effects: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    return {
        "unit": unit,
        "compose": "leaf",
        "completion": "observed",
        "repeat": "safe",
        "budget": budget,
        "env_key_field": env_key_field,
        "declared_codes": [],
        "preconditions": [],
        "postcondition": "done",
        "wait": {"poll_every": 1.0, "backoff": 1.0, "max_wait": 5.0},
        "resource_kind": "thing",
        "may_touch": [],
        "effects": [dict(e) for e in effects],
        "retryable": [],
        "remedies": [],
        "max_attempts": 1,
    }


def create_run_effect(name: str = "create", release_timeout: float | None = 5.0) -> dict[str, Any]:
    return {
        "effect": name,
        "facet": "create",
        "verb": "",
        "lifetime": "run",
        "host_sections": [],
        "release_timeout": release_timeout,
        "is_release": False,
    }


def child_ref(
    name: str,
    path: str | None,
    *,
    unit: str | None = None,
    params: Mapping[str, Any] | None = None,
    needs: Sequence[str] = (),
    vantage: str = "host",
) -> dict[str, Any]:
    return {
        "name": name,
        "binding": {
            "unit": unit if unit is not None else name,
            "params": dict(params or {}),
            "needs": list(needs),
            "vantage": vantage,
        },
        "path": path,
    }


def all_node(
    unit: str,
    children: Sequence[Mapping[str, Any]],
    *,
    budget: float | None = 60.0,
    concurrency: int = 1,
    gates: Sequence[str] = (),
    identifier_sets: Mapping[str, Sequence[str]] | None = None,
    arg_bindings: Sequence[Mapping[str, Any]] = (),
    env_key_field: str | None = None,
) -> dict[str, Any]:
    return {
        "unit": unit,
        "compose": "all",
        "completion": "observed",
        "repeat": "safe",
        "budget": budget,
        "env_key_field": env_key_field,
        "declared_codes": [],
        "children": [dict(c) for c in children],
        "concurrency": concurrency,
        "gates": list(gates),
        "identifier_sets": {k: sorted(v) for k, v in (identifier_sets or {}).items()},
        "arg_bindings": [dict(b) for b in arg_bindings],
    }


def alternative(
    unit: str,
    path: str | None,
    *,
    reachable_from: Sequence[str] = ("container", "host"),
) -> dict[str, Any]:
    return {
        "unit": unit,
        "realization": "docker_service",
        "reachable_from": sorted(reachable_from),
        "human_action": None,
        "path": path,
    }


def choice_node(
    unit: str,
    alternatives: Sequence[Mapping[str, Any]],
    *,
    budget: float | None = 30.0,
    select_arg: str | None = None,
    fallback: str | None = None,
) -> dict[str, Any]:
    return {
        "unit": unit,
        "compose": "choice",
        "completion": "observed",
        "repeat": "safe",
        "budget": budget,
        "env_key_field": None,
        "declared_codes": [],
        "choice": {
            "logical_system": unit,
            "alternatives": [dict(a) for a in alternatives],
            "select_arg": select_arg,
            "fallback": fallback,
            "readiness": "ready",
        },
    }


def tree(root: str, nodes: Mapping[str, Mapping[str, Any]]) -> DeclaredTree:
    return DeclaredTree.build(root, nodes)


def wrap_node(declared: DeclaredTree, target: str) -> tuple[DeclaredTree, dict[str, str]] | None:
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


def _shapes(depth: int) -> list[Shape]:
    """Every shape of depth <= `depth` (a leaf is depth 1), fan-out <= 2."""
    if depth <= 1:
        return [LEAF]
    below = _shapes(depth - 1)
    out: list[Shape] = [LEAF, ("choice", 1), ("choice", 2)] if depth >= 2 else [LEAF]
    for a in below:
        out.append(("all", (a,)))
        for b in below:
            out.append(("all", (a, b)))
    seen: list[Shape] = []
    for shape in out:
        if shape not in seen:
            seen.append(shape)
    return seen


def build(shape: Shape, *, needs_edge: bool = False, share: int | None = None) -> DeclaredTree:
    """The tree for `shape`. `needs_edge` makes the root's second child need its first;
    `share` (an index >= 1 into the leaf occurrences in depth-first order) turns that occurrence
    into a second reference to the first leaf (one shared node)."""
    nodes: dict[str, dict[str, Any]] = {}
    leaf_paths: list[tuple[str, str]] = []  # (canonical path, name) per placed leaf, in order
    counter = [0]

    def fresh(prefix: str) -> str:
        counter[0] += 1
        return f"{prefix}{counter[0]}"

    def place(sh: Shape, path: str, unit: str) -> None:
        if sh[0] == "leaf":
            nodes[path] = leaf_node(unit)
            leaf_paths.append((path, unit))
        elif sh[0] == "choice":
            alts = []
            for _ in range(sh[1]):
                alt = fresh("alt")
                apath = f"{path}/{alt}" if path else alt
                nodes[apath] = leaf_node(alt)
                leaf_paths.append((apath, alt))
                alts.append(alternative(alt, apath))
            nodes[path] = choice_node(unit, alts)
        else:
            refs = []
            names = [fresh("n") for _ in sh[1]]
            for i, (child, name) in enumerate(zip(sh[1], names, strict=True)):
                cpath = f"{path}/{name}" if path else name
                needs = [names[0]] if (needs_edge and path == ROOT_PATH and i == 1) else []
                refs.append(child_ref(name, cpath, needs=needs))
                place(child, cpath, name)
            nodes[path] = all_node(unit, refs)

    place(shape, ROOT_PATH, "root")
    if share is not None and 1 <= share < len(leaf_paths):
        target_path, target_name = leaf_paths[0]
        dup_path, dup_name = leaf_paths[share]
        for node in nodes.values():
            for ref in node.get("children", ()):
                if ref["path"] == dup_path:
                    ref["name"] = target_name
                    ref["binding"]["unit"] = target_name
                    ref["path"] = target_path
                    # a needs edge onto the renamed sibling follows the name
                    for other in node["children"]:
                        other["binding"]["needs"] = [
                            target_name if n == dup_name else n for n in other["binding"]["needs"]
                        ]
            for alt in node.get("choice", {}).get("alternatives", ()):
                if alt["path"] == dup_path:
                    alt["path"] = target_path
                    alt["unit"] = target_name
        del nodes[dup_path]
    return DeclaredTree.build("root", nodes)


def _leaf_count(shape: Shape) -> int:
    if shape[0] == "leaf":
        return 1
    if shape[0] == "choice":
        return shape[1]
    return sum(_leaf_count(s) for s in shape[1])


def all_trees() -> Iterator[tuple[str, DeclaredTree]]:
    """(label, tree) for every generated tree."""
    for index, shape in enumerate(_shapes(3)):
        two_children = shape[0] == "all" and len(shape[1]) == 2
        for needs_edge in (False, True) if two_children else (False,):
            yield f"s{index}-n{int(needs_edge)}", build(shape, needs_edge=needs_edge)
            for share in range(1, _leaf_count(shape)):
                try:
                    yield (
                        f"s{index}-n{int(needs_edge)}-share{share}",
                        build(shape, needs_edge=needs_edge, share=share),
                    )
                except Exception as exc:  # a shape whose sharing is not a well-formed tree
                    raise AssertionError(f"generator built a bad tree for {shape} {share}") from exc
