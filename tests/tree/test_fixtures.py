"""L.TR-0.1: the structural fake tree fixture set (MC-B3-01) under `tests/fixtures/trees/`.

Every module under that directory (except the generators, L.TR-0.6) is a single-file plugin that
exports `ENTRY` and `LABEL = {vertices, depth, shared, expect}` and imports only `trestle.plugin`,
`trestle.workflow` and `trestle_packs.fakes` (DM-09). This file checks every one of them, so each
behaviour fixture a later leaf adds is checked the moment it lands."""

from __future__ import annotations

import ast
import importlib.util
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from trestle.server.plugin_validate import PublicationRefused
from trestle.server.snapshots import load_declared_tree, materialize_snapshot
from trestle.workflow import AllDeclaration, ChoiceNode, LeafDeclaration

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


def _declaration(entry: Any, unit: str) -> Any:
    """A unit's declaration (a composite is its own data; a leaf answers `declare()`), or None
    when the entry does not resolve the name."""
    found = entry.units.get(unit)
    if found is None or isinstance(found, AllDeclaration | ChoiceNode | LeafDeclaration):
        return found
    return found.declare()


def inspect_entry(entry: Any) -> tuple[int, int, str | None]:
    """(vertices, depth, shared) of `entry`'s declared tree, by inspection of its declaration
    objects. `vertices` counts the distinct logical nodes referenced (an alternative of a choice
    and an unresolvable reference each once); `depth` is the longest containment chain (a leaf is
    depth 1); `shared` is the one node referenced from two different parents, or None."""
    parents: dict[str, set[str]] = {}
    depth_of: dict[str, int] = {}

    def children_of(unit: str) -> list[str]:
        decl = _declaration(entry, unit)
        if isinstance(decl, AllDeclaration):
            return [child.unit for child in decl.children]
        if isinstance(decl, ChoiceNode):
            return [alt.unit for alt in decl.choice.alternatives]
        return []

    def walk(unit: str, above: tuple[str, ...]) -> int:
        assert unit not in above, f"containment cycle through {unit}"
        depth = 1 + max((walk(child, (*above, unit)) for child in children_of(unit)), default=0)
        depth_of[unit] = depth
        for child in children_of(unit):
            parents.setdefault(child, set()).add(unit)
        return depth

    depth = walk(entry.root, ())
    shared = sorted(name for name, above in parents.items() if len(above) > 1)
    assert len(shared) <= 1, f"more than one shared node: {shared}"
    return len(depth_of), depth, (shared[0] if shared else None)


def test_the_structural_set_is_present() -> None:
    assert STRUCTURAL_SET <= {p.stem for p in fixture_paths()}


@pytest.mark.parametrize("path", fixture_paths(), ids=lambda p: p.stem)
def test_every_fixture_publishes_and_declares_its_label(path: Path, tmp_path: Path) -> None:
    module = load_fixture(path)
    label = module.LABEL
    assert set(label) == LABEL_KEYS, path.stem
    assert label["expect"] == "valid" or label["expect"] in GROUND_CODES, path.stem

    # its ENTRY's declaration objects (MC-24 types) match its label by inspection
    vertices, depth, shared = inspect_entry(module.ENTRY)
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
