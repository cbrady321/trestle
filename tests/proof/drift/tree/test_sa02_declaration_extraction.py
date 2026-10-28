"""SA-02 in A-2 (L.TR-0.2): the resolved declared tree keeps SV-2's wire shape. Every descendant is
one node at its canonical path with exactly its compose's field set, every child reference of a
unit the entry resolves names a recorded node, every recorded node but the root is named, and the
format is unchanged (MC-34, no bump). The check fails on a planted extra key, a planted unresolved
child path and a planted format bump (DM-28)."""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from typing import Any

import pytest

from tests.fixtures.trees import generators
from trestle.common.plan import ROOT_PATH, DeclaredTree, UnknownDeclaredFormat
from trestle.common.plan.declared import FORMAT_VERSION, NODE_KEYS
from trestle.workflow import WorkflowEntry
from trestle.workflow.extract import extract_declared_tree

RESOLVED_FIXTURES = ("three_level", "shared_diamond", "slice_a_tree", "two_branch_barrier")


def _refs(node: Mapping[str, Any]) -> list[tuple[dict[str, Any], str]]:
    if node["compose"] == "all":
        return [(c, c["binding"]["unit"]) for c in node["children"]]
    if node["compose"] == "choice":
        return [(a, a["unit"]) for a in node["choice"]["alternatives"]]
    return []


def shape_problems(nodes: Mapping[str, Mapping[str, Any]], entry: WorkflowEntry) -> list[str]:
    """What is wrong with `nodes` as the resolved declared tree of `entry`; empty when nothing."""
    problems: list[str] = []
    named: set[str] = set()
    for path, node in nodes.items():
        if set(node) != NODE_KEYS[node["compose"]]:
            problems.append(f"{path!r}: key set {sorted(set(node) ^ NODE_KEYS[node['compose']])}")
        for ref, unit in _refs(node):
            child = ref["path"]
            if child is None:
                if unit in entry.units:
                    problems.append(f"{path!r}: child {unit!r} resolves but its path is null")
            elif child not in nodes:
                problems.append(f"{path!r}: child path {child!r} names no node")
            else:
                named.add(child)
    if named != set(nodes) - {ROOT_PATH}:
        problems.append(f"nodes never named: {sorted(set(nodes) - named - {ROOT_PATH})}")
    return problems


@pytest.mark.parametrize("sa", ["SA-02"])
def test_descendant_extraction_shape(sa: str) -> None:
    for name in RESOLVED_FIXTURES:
        entry = generators.fixture_tree(name).entry
        tree = extract_declared_tree(entry)
        assert tree.format_version == FORMAT_VERSION == 1
        assert shape_problems(tree.nodes, entry) == [], name
        assert DeclaredTree.from_json(tree.to_json()) == tree  # the wire round trip keeps it

    entry = generators.fixture_tree("three_level").entry
    tree = extract_declared_tree(entry)

    # a planted extra key is a shape problem (and the loader itself refuses the tree)
    extra = copy.deepcopy(dict(tree.nodes))
    extra["web"]["surprise"] = 1  # type: ignore[index]
    assert shape_problems(extra, entry)

    # a planted unresolved child path is a shape problem
    unresolved = copy.deepcopy(dict(tree.nodes))
    unresolved["data"]["children"][0]["path"] = None  # type: ignore[index]
    assert any("path is null" in p for p in shape_problems(unresolved, entry))

    # a planted format bump is refused by name
    data = json.loads(tree.to_json())
    data["format_version"] = FORMAT_VERSION + 1
    with pytest.raises(UnknownDeclaredFormat):
        DeclaredTree.from_json(json.dumps(data))
