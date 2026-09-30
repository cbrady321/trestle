"""L.TR-0.1: the structural fake tree fixture set (MC-B3-01) under `tests/fixtures/trees/`.

Every module under that directory (except the generators, L.TR-0.6) is a single-file plugin that
exports `ENTRY` and `LABEL = {vertices, depth, shared, expect}` and imports only `trestle.plugin`,
`trestle.workflow` and `trestle_packs.fakes` (DM-09). This file checks every one of them, so each
behaviour fixture a later leaf adds is checked the moment it lands."""

from __future__ import annotations

import ast
import dataclasses
import importlib.util
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tests.fixtures.trees import generators
from tests.fixtures.trees.generators import measure
from trestle.server.plugin_validate import PublicationRefused
from trestle.server.snapshots import load_declared_tree, materialize_snapshot
from trestle.workflow import AllDeclaration

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "tests" / "fixtures" / "trees"
NOT_FIXTURES = {"__init__.py", "generators.py"}

# The structural set MC-B3-01 names (L.TR-0.1); behaviour fixtures join the directory later.
STRUCTURAL_SET = {
    "two_branch_barrier", "three_step_retry_remedy", "shared_diamond", "three_level",
    "slice_a_tree", "lease_pair", "identifier_sets", "choice_fake", "root_eligible_both",
    "sibling_only_leaf", "race_two_trigger", "cycle", "conflict", "misfit",
    "unknown_descendant", "uncovered_precondition",
}  # fmt: skip

# `expect` is "valid" or the ground the tree is defective in. A ground fixture publishes, or is
# refused with its ground's code: L.TR-0.4 and L.TR-1.5 own the specific publication refusals
# (misfit is admission's, L.TR-1.3, so it always publishes).
GROUND_CODES: dict[str, frozenset[str]] = {
    "cycle": frozenset({"publication.dependency_cycle"}),
    "conflict": frozenset({"publication.declaration_conflict"}),
    "unknown_descendant": frozenset({"publication.unit_unresolved"}),
    "uncovered_precondition": frozenset({"publication.plan_precondition_uncovered"}),
    "misfit": frozenset(),
}

# Project roots a fixture may import (DM-09); the standard library is unrestricted.
ALLOWED_ROOTS = ("trestle.plugin", "trestle.workflow", "trestle_packs.fakes")

LABEL_KEYS = {"vertices", "depth", "shared", "expect"}


def fixture_paths() -> list[Path]:
    return sorted(p for p in FIXTURES.glob("*.py") if p.name not in NOT_FIXTURES)


def load_fixture(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"tree_fixture_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_structural_set_is_present() -> None:
    assert STRUCTURAL_SET <= {p.stem for p in fixture_paths()}


@pytest.mark.parametrize("path", fixture_paths(), ids=lambda p: p.stem)
def test_every_fixture_publishes_and_declares_its_label(path: Path, tmp_path: Path) -> None:
    module = load_fixture(path)
    label = module.LABEL
    assert set(label) == LABEL_KEYS, path.stem
    assert label["expect"] == "valid" or label["expect"] in GROUND_CODES, path.stem

    # its ENTRY's declaration objects (MC-24 types) match its label by inspection
    vertices, depth, shared = measure(module.ENTRY)
    assert (vertices, depth, shared) == (label["vertices"], label["depth"], label["shared"]), (
        path.stem
    )
    assert depth <= 3, path.stem  # the plan's depth bound (L.TR-0.6 generators keep to it too)

    # a valid fixture publishes through the validator; a ground fixture publishes or is refused
    # with its ground's code, never with another
    home = tmp_path / "home"
    try:
        snap = materialize_snapshot(path, path.stem, home=home)
    except PublicationRefused as refused:
        assert label["expect"] != "valid", (path.stem, refused.code, str(refused))
        assert refused.code in GROUND_CODES[label["expect"]], (path.stem, refused.code)
        assert not (home / "snapshots").exists() or not any((home / "snapshots").iterdir())
    else:
        tree = load_declared_tree(snap)
        assert tree is not None and tree.root == module.ENTRY.root, path.stem


def _imports(tree: ast.AST) -> Iterator[str]:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "a fixture has no relative import"
            yield node.module or ""


@pytest.mark.parametrize("path", fixture_paths(), ids=lambda p: p.stem)
def test_fixture_imports_allowed_roots_only(path: Path) -> None:
    for name in _imports(ast.parse(path.read_text(encoding="utf-8"))):
        if name.split(".")[0] not in {"trestle", "trestle_packs"}:
            continue  # standard library
        assert name == "trestle.plugin" or any(
            name == root or name.startswith(root + ".") for root in ALLOWED_ROOTS
        ), (path.stem, name)


def test_the_import_check_rejects_a_planted_import() -> None:
    planted = ast.parse("from trestle.server.main import create_kernel\n")
    (name,) = list(_imports(planted))
    assert name == "trestle.server.main"
    assert not any(name == root or name.startswith(root + ".") for root in ALLOWED_ROOTS)


# ---- L.TR-0.6: the deterministic generators and the tree conftest

SEEDS = generators.PERMUTE_SEEDS


def test_generators_deterministic_per_seed() -> None:
    first = [generators.permute(seed) for seed in SEEDS]
    again = [generators.permute(seed) for seed in SEEDS]
    assert [t.source.encode() for t in first] == [
        t.source.encode() for t in again
    ]  # byte-identical
    assert len({t.source for t in first}) == len(SEEDS)  # different across seeds
    assert len({t.name for t in first}) == len(SEEDS)
    # the shuffle is real: some seed of a fixture with siblings reorders a composite's children
    reordered = 0
    for seed, tree in zip(SEEDS, first, strict=True):
        base = generators.fixture_tree(generators.PERMUTABLE[seed % len(generators.PERMUTABLE)])
        for unit in tree.entry.units:
            new = generators.declaration_of(tree.entry, unit)
            old = generators.declaration_of(base.entry, unit)
            if isinstance(new, AllDeclaration) and new.children != old.children:
                reordered += 1
    assert reordered

    for fixture, node, name in generators.WRAPPINGS:
        one = generators.wrap_deeper(generators.fixture_tree(fixture), node, name)
        two = generators.wrap_deeper(generators.fixture_tree(fixture), node, name)
        assert one.source == two.source and one == two
    assert generators.hundred_node(100) == generators.hundred_node(100)
    assert generators.hundred_node(100).source != generators.hundred_node(3).source


def test_wrap_deeper_names_the_wrapper_and_keeps_the_needs() -> None:
    barrier = generators.fixture_tree("two_branch_barrier")
    wrapped = generators.wrap_deeper(barrier, "left", "aaa_wrap")
    assert (wrapped.vertices, wrapped.depth) == (barrier.vertices + 1, barrier.depth + 1)
    root = generators.declaration_of(wrapped.entry, wrapped.entry.root)
    assert [c.unit for c in root.children] == ["aaa_wrap", "right", "join"]
    assert dict((c.unit, c.needs) for c in root.children)["join"] == ("aaa_wrap", "right")
    inner = generators.declaration_of(wrapped.entry, "aaa_wrap")
    assert [c.unit for c in inner.children] == ["left"]
    with pytest.raises(ValueError, match="bound exactly once"):
        generators.wrap_deeper(generators.fixture_tree("shared_diamond"), "shared", "x")


def _published(tree: generators.Tree, tmp_path: Path) -> Any:
    home = tmp_path / f"home-{tree.name}"
    plugin = tree.write(tmp_path)
    return materialize_snapshot(plugin, tree.name, home=home)


def _carved(tree: generators.Tree) -> tuple[Any, Any]:
    from trestle.common import clock
    from trestle.common.plan import carving, compiler

    plan = compiler.compile(generators.declared_of(tree.entry), {})
    assert isinstance(plan, compiler.AdmittedPlan), (tree.name, plan)
    slices = carving.carve(
        plan,
        tree.entry.deadline.total_seconds(),
        clock.FINALIZATION_RESERVE_S,
        carving.release_slice_for(plan, clock.release_slice),
        deadline_ceiling_s=clock.deadline_ceiling,
    )
    return plan, slices


def test_generated_trees_publish(tmp_path: Path) -> None:
    from trestle.common.plan import compiler

    assert len({t.name for t in generators.GENERATED}) == len(generators.GENERATED)
    for tree in generators.GENERATED:
        module = tree.load()
        assert module.LABEL == tree.label
        assert tree.depth <= 3, tree.name  # every generated tree keeps to the plan's depth bound
        # the source satisfies MC-B3-01's import rule
        for name in _imports(ast.parse(tree.source)):
            assert name.split(".")[0] not in {"trestle", "trestle_packs"} or any(
                name == root or name.startswith(root + ".") for root in ALLOWED_ROOTS
            ), (tree.name, name)
        # it publishes through the validator, and the label agrees with what MC-23 compiles
        snap = _published(tree, tmp_path)
        assert load_declared_tree(snap) is not None
        plan, slices = _carved(tree)
        assert len(plan.vertices) == tree.vertices, tree.name
        assert not isinstance(slices, compiler.Refusal), (tree.name, slices)

    # both hundred_node trees compile under MC-23 with no BUDGET_DOES_NOT_FIT (B2-C2 (4): root
    # budget >= ceil(width / B) * leaf budget, checked for width 100 and width 3 alike)
    for width in (100, 3):
        tree = generators.hundred_node(width)
        assert tree in generators.GENERATED
        plan, slices = _carved(tree)
        assert len(plan.vertices) == width + 1
        assert not isinstance(slices, compiler.Refusal), (width, slices)


def test_tree_kernel_isolated(tree_kernel: Any, tmp_path: Path, monkeypatch: Any) -> None:
    import os

    home = tree_kernel.home
    assert home == tmp_path / "home"
    assert os.environ["TRESTLE_HOME"] == str(home)
    marker = home / "isolation-marker"
    assert not marker.exists()  # nothing another test left
    marker.write_text(str(home), encoding="utf-8")
    assert home not in _HOMES_SEEN  # no earlier test used this home
    _HOMES_SEEN.append(home)


_HOMES_SEEN: list[Path] = []


def test_tree_kernel_second_use_gets_another_home(tree_kernel: Any) -> None:
    assert tree_kernel.home not in _HOMES_SEEN
    assert not (tree_kernel.home / "isolation-marker").exists()
    _HOMES_SEEN.append(tree_kernel.home)


def test_scale_only_reduces_by_sibling_count() -> None:
    assert generators.HUNDRED_NODE_CONCURRENCY == 2
    assert set(generators.SCALE_ONLY) == {generators.hundred_node(100)}
    for big, small in generators.SCALE_ONLY.items():
        assert big in generators.GENERATED and small in generators.GENERATED
        big_root = generators.declaration_of(big.entry, big.entry.root)
        small_root = generators.declaration_of(small.entry, small.entry.root)
        # the same root concurrency B, and B < the value's sibling count
        assert big_root.concurrency == small_root.concurrency == generators.HUNDRED_NODE_CONCURRENCY
        assert generators.HUNDRED_NODE_CONCURRENCY < len(small_root.children)
        assert len(big_root.children) == 100 and len(small_root.children) == 3
        # the same leaf declaration and flags, and the same depth
        assert generators.declaration_of(big.entry, "node") == generators.declaration_of(
            small.entry, "node"
        )
        assert big_root.flags == small_root.flags and big.depth == small.depth
        # no needs among the siblings, and the trees differ only in the root's child list
        assert all(not child.needs for child in (*big_root.children, *small_root.children))
        assert dataclasses.replace(big_root, children=small_root.children) == small_root
        assert set(big.entry.units) == set(small.entry.units)
        assert big.entry.deadline == small.entry.deadline
