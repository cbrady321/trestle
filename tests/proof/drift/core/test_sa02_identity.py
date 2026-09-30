"""SA-02 in core (L.CL-C1.4): the snapshot identity's composition (MC-18) is defined once, in
`trestle.common.ids.generate_snapshot_id`; `manifest.json`'s `declared` and `entry` are read only
through `snapshots.load_declared` (`DeclaredMetadata.from_manifest` behind it); and the identity
function takes every ingredient MC-18 names and no other."""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from trestle.common import ids

ROOT = Path(__file__).resolve().parents[4]
# MC-18: source, declared package digests, input and return schema, declared metadata (with the
# summary budget), the declared-tree slot, the runtime version.
INGREDIENTS = {
    "source_sha256",
    "package_digests",
    "input_schema_sha256",
    "return_schema_sha256",
    "declared",
    "summary_budget",
    "declared_tree",
    "runtime_version",
}
# The only modules that may read a manifest's declared block or entry name.
MANIFEST_READERS = {"trestle/common/types.py", "trestle/server/snapshots.py"}


@pytest.mark.parametrize("sa", ["SA-02"])
def test_identity_function_takes_exactly_the_mc18_ingredients(sa: str) -> None:
    params = inspect.signature(ids.generate_snapshot_id).parameters
    assert set(params) == INGREDIENTS
    assert all(p.kind is inspect.Parameter.KEYWORD_ONLY for p in params.values())
    assert params["declared_tree"].default == ids.DECLARED_TREE_SLOT_EMPTY == ""


@pytest.mark.parametrize("sa", ["SA-02"])
def test_one_definition_and_one_caller_of_the_identity(sa: str) -> None:
    defined: list[str] = []
    called: list[str] = []
    for path in sorted((ROOT / "trestle").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        rel = str(path.relative_to(ROOT))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "generate_snapshot_id":
                defined.append(rel)
            if isinstance(node, ast.Call):
                func = node.func
                name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
                if name == "generate_snapshot_id":
                    called.append(rel)
    assert defined == ["trestle/common/ids.py"]
    assert called == ["trestle/server/snapshots.py"]


@pytest.mark.parametrize("sa", ["SA-02"])
def test_declared_and_entry_are_read_only_through_the_carrier(sa: str) -> None:
    readers: list[str] = []
    for path in sorted((ROOT / "trestle").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            key: object = None
            if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
                key = node.slice.value
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and node.args
                and isinstance(node.args[0], ast.Constant)
            ):
                key = node.args[0].value
            if key in ("declared", "package_digests"):
                readers.append(str(path.relative_to(ROOT)))
    assert set(readers) <= MANIFEST_READERS, sorted(set(readers) - MANIFEST_READERS)
    assert readers  # the scan is not vacuous: the carrier itself is found
