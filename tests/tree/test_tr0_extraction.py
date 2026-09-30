"""L.TR-0.2: the validator resolves every descendant binding into MC-34 `declaration.json`
(SV-2's field set, no format bump), `WR-UNIT-8:declared-tree-extracted`.

A composite root is recorded with one node per declaration path and every child reference naming
its node's canonical path (V-1.2: a logical node `(unit, name)` is placed at its first occurrence
in depth-first declaration order and every other occurrence is an alias naming that path)."""

from __future__ import annotations

import dataclasses
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from tests.fixtures.trees import generators
from tests.single.contract import declared_fixtures as fx
from trestle.common.plan import ROOT_PATH, DeclaredTree, compiler
from trestle.common.plan.declared import FORMAT_VERSION, NODE_KEYS
from trestle.common.types import RequestOutcome
from trestle.server.snapshots import load_declared_tree, materialize_snapshot
from trestle.workflow import ChildBinding
from trestle.workflow.extract import extract_declared_tree, extract_root

REPO = Path(__file__).resolve().parents[2]
TREES = REPO / "tests" / "fixtures" / "trees"
SPINE_LEAF = REPO / "tests" / "fixtures" / "workflows" / "spine_leaf.py"

# `extract_declared_tree` of a leaf-root entry as `wr-ckpt/single` produced it: the SV-2 extractor
# before descendants were resolved (measured on the A-1 bundle head, wr/tree-bundle/lane-tr0a).
SPINE_LEAF_SHA256 = "63b4ec1039272c30610e81c06bf81dd97ad432745bb67763b1d99007f28681a5"
SPINE_LEAF_DIGEST = "df1c29c802b30eeb7a6869107b3f55690eb3192d19e937193c45279a2210efda"
FX_LEAF_SHA256 = "12692c341d4a65c9eb150c30a23d2e1218d82e335eab6c6e12483143a28f6c50"

# the WR-PLAN-12 elements every leaf node carries (the sixth, `repeat`, is the common field)
PLAN_12_ELEMENTS = {
    "preconditions",
    "postcondition",
    "wait",
    "resource_kind",
    "may_touch",
    "repeat",
}


def _fixture(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"tree_fixture_{name}", TREES / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _published(name: str, tmp_path: Path) -> DeclaredTree:
    snap = materialize_snapshot(TREES / f"{name}.py", name, home=tmp_path / "home")
    tree = load_declared_tree(snap)
    assert tree is not None
    return tree


def _children(node: dict) -> dict[str, str | None]:
    return {c["name"]: c["path"] for c in node["children"]}


@pytest.mark.proves("WR-UNIT-8", "WR-UNIT-8:declared-tree-extracted", "A", "tree", "LOGIC", "CI")
def test_descendants_resolved_three_levels(tmp_path: Path) -> None:
    tree = _published("three_level", tmp_path)
    assert sorted(tree.nodes) == [ROOT_PATH, "data", "data/cache", "data/db", "web"]
    # one node per declaration path with SV-2's field set: exactly the compose's key set
    for path, node in tree.nodes.items():
        assert set(node) == NODE_KEYS[node["compose"]], path
    for path in ("data/cache", "data/db", "web"):
        assert tree.nodes[path]["compose"] == "leaf"
        assert PLAN_12_ELEMENTS | {"repeat"} <= set(tree.nodes[path]), path  # the six elements
    # children resolved to the paths of the nodes they name
    assert _children(tree.nodes[ROOT_PATH]) == {"data": "data", "web": "web"}
    assert _children(tree.nodes["data"]) == {"db": "data/db", "cache": "data/cache"}
    assert tree.nodes["data"]["children"][1]["binding"]["needs"] == ["db"]
    # every path a child names is a recorded node, and every node but the root is named by one
    named = {
        path
        for node in tree.nodes.values()
        if node["compose"] == "all"
        for path in _children(node).values()
    }
    assert named == set(tree.nodes) - {ROOT_PATH}
    assert tree.root == "app" and tree.format_version == FORMAT_VERSION


@pytest.mark.proves("WR-UNIT-8", "WR-UNIT-8:declared-tree-extracted", "A", "tree", "LOGIC", "CI")
def test_shared_diamond_both_paths_recorded(tmp_path: Path) -> None:
    tree = _published("shared_diamond", tmp_path)
    # the shared node is one node at its first depth-first occurrence; the alias under the other
    # parent is recorded as a child reference to that same path (V-1.2)
    assert sorted(tree.nodes) == [ROOT_PATH, "left", "left/shared", "right"]
    assert _children(tree.nodes["left"]) == {"shared": "left/shared"}
    assert _children(tree.nodes["right"]) == {"shared": "left/shared"}
    assert tree.nodes["right"]["children"][0]["binding"]["params"] == {"mode": "common"}
    assert tree.nodes["left/shared"]["compose"] == "leaf"


@pytest.mark.proves("WR-UNIT-8", "WR-UNIT-8:declared-tree-extracted", "A", "tree", "LOGIC", "CI")
def test_choice_alternatives_are_resolved_under_their_choice(tmp_path: Path) -> None:
    tree = _published("slice_a_tree", tmp_path)
    assert sorted(tree.nodes) == [ROOT_PATH, "api", "db", "db/db_docker", "db/db_ide", "web"]
    alts = tree.nodes["db"]["choice"]["alternatives"]
    assert [a["path"] for a in alts] == ["db/db_docker", "db/db_ide"]
    assert tree.nodes["db"]["choice"]["fallback"] == "db_docker"


def test_unresolvable_name_stays_an_unresolved_name(tmp_path: Path) -> None:
    # a name that resolves to no unit keeps `path: null` and no node (admission refuses it, TR-1.1);
    # publication is unchanged in this leaf (L.TR-0.4 refuses it there)
    tree = _published("unknown_descendant", tmp_path)
    assert list(tree.nodes) == [ROOT_PATH]
    assert _children(tree.nodes[ROOT_PATH]) == {"ghost": None}


def test_one_name_bound_to_two_units_gets_distinct_paths() -> None:
    # one logical node bound twice (same unit and name, different literal parameters) is one node
    # (its conflict is the compiler's, L.TR-0.4 / L.TR-1.2) ...
    entry = _fixture("conflict").ENTRY
    tree = extract_declared_tree(entry)
    assert sorted(tree.nodes) == [ROOT_PATH, "worker"]
    assert _children(tree.nodes[ROOT_PATH]) == {"worker": "worker"}
    # ... but one name bound to two different units is two logical nodes: the later one gets a
    # `#<n>` suffix, so the compiler sees two nodes under one name and refuses it
    clash = generators.declaration_of(entry, "clash")
    two_units = dataclasses.replace(
        clash,
        children=(
            ChildBinding(unit="worker", params={}, needs=(), name="x"),
            ChildBinding(unit="other", params={}, needs=(), name="x"),
        ),
    )
    units = {**entry.units, "clash": two_units, "other": entry.units["worker"]}
    split = extract_declared_tree(dataclasses.replace(entry, units=units))
    assert [c["path"] for c in split.nodes[ROOT_PATH]["children"]] == ["x", "x#2"]
    assert sorted(split.nodes) == [ROOT_PATH, "x", "x#2"]
    refused = compiler.compile(split, {})
    assert (
        isinstance(refused, compiler.Refusal) and refused.code == "admission.declaration_conflict"
    )


def test_leaf_root_declaration_byte_identical() -> None:
    # a leaf root extracts exactly as it did at wr-ckpt/single: same bytes, same digest
    spec = importlib.util.spec_from_file_location("spine_leaf_bytes", SPINE_LEAF)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    spine = extract_declared_tree(module.ENTRY)
    assert list(spine.nodes) == [ROOT_PATH]
    assert hashlib.sha256(spine.to_json().encode()).hexdigest() == SPINE_LEAF_SHA256
    assert spine.digest == SPINE_LEAF_DIGEST
    leaf = extract_declared_tree(fx.leaf_entry())
    assert hashlib.sha256(leaf.to_json().encode()).hexdigest() == FX_LEAF_SHA256


@pytest.mark.proves("WR-UNIT-8", "WR-UNIT-8:declared-tree-extracted", "A", "tree", "LOGIC", "CI")
def test_no_format_bump(tmp_path: Path) -> None:
    for name in ("three_level", "shared_diamond", "slice_a_tree"):
        tree = _published(name, tmp_path)
        assert tree.format_version == FORMAT_VERSION == 1
        data = json.loads(tree.to_json())
        assert set(data) == {"format_version", "root", "nodes", "digest"}
        assert DeclaredTree.from_json(tree.to_json()) == tree
    # the resolved wire keys are the SV-2 ones: a composite node adds no key of its own
    assert set(_published("three_level", tmp_path).nodes[ROOT_PATH]) == NODE_KEYS["all"]


_CHILD_PROGRAM = """
import importlib.util, sys
spec = importlib.util.spec_from_file_location("plugin", sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
from trestle.workflow.extract import extract_root
sys.stdout.write(extract_root(module.ENTRY)[1].to_json())
"""


@pytest.mark.parametrize("name", ["three_level", "shared_diamond", "slice_a_tree"])
def test_validator_and_child_extract_identical_tree(name: str, tmp_path: Path) -> None:
    published = _published(name, tmp_path)
    # the child side re-derives the tree in its own process (B1-O3), under another hash seed
    env = {**os.environ, "PYTHONPATH": str(REPO), "PYTHONHASHSEED": "4242"}
    proc = subprocess.run(
        [sys.executable, "-c", _CHILD_PROGRAM, str(TREES / f"{name}.py")],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    assert proc.stdout == published.to_json()
    assert extract_root(_fixture(name).ENTRY)[1] == published


def test_the_server_never_imports_the_plugin(tree_kernel, tmp_path: Path) -> None:  # noqa: ANN001
    plugin = tmp_path / "three_level.py"
    plugin.write_text((TREES / "three_level.py").read_text(encoding="utf-8"), encoding="utf-8")
    before = set(sys.modules)
    published = tree_kernel.registry.publish_source(
        plugin.read_text(encoding="utf-8"), name="three_level"
    )
    assert not isinstance(published, RequestOutcome), published
    assert tree_kernel.registry.get("three_level") is not None
    loaded = [
        name
        for name in set(sys.modules) - before
        if getattr(sys.modules[name], "__file__", None)
        and Path(sys.modules[name].__file__).name in {"three_level.py", "plugin.py"}  # type: ignore[arg-type]
    ]
    assert loaded == []
    assert "trestle_plugin_validate" not in sys.modules
