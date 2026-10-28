"""The run-time plan enumerator of the tree band (L.TR-3.7; MC-B3-01).

`valid_plans(kind)` reads, at the time it is called, every module under `tests/fixtures/trees/`
(the only directory that holds MC-B3-01 fixtures) that exports a `LABEL` with `expect = "valid"`,
plus every tree of `generators.GENERATED` (L.TR-0.6), and splits them by kind: `choice` iff the plan
holds a `ChoiceNode` at any depth, else `nonchoice`. Nothing is listed by name: a fixture a later
leaf adds is enumerated at the next run with no edit to either termination suite (A2c2-3), which
is what makes 'every valid plan halts' a claim about the fixture set rather than about a list.

`directory` names another fixture directory (a temporary copy with a planted fixture, for the
enumerator's own test); `GENERATED` is always included."""

from __future__ import annotations

import importlib.util
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tests.fixtures.trees import generators
from trestle.workflow import AllDeclaration, ChoiceNode, LeafDeclaration, WorkflowEntry

FIXTURES = Path(generators.__file__).resolve().parent
NOT_FIXTURES = {"__init__.py", "generators.py"}
KINDS = ("nonchoice", "choice")


@dataclass(frozen=True)
class Plan:
    """One enumerated tree: where it came from, its declaration and the label it carries."""

    name: str
    entry: WorkflowEntry
    label: dict[str, Any]
    source: str  # "fixture" or "generated"

    @property
    def kind(self) -> str:
        return "choice" if _has_choice(self.entry) else "nonchoice"

    @property
    def request(self) -> dict[str, str]:
        """The arguments a root that names an environment argument needs to compile (each
        `env_key_field` any node declares, given a value)."""
        request: dict[str, str] = {}
        for name in self.entry.units:
            declared = _declaration(self.entry, name)
            key = getattr(declared, "env_key_field", None)
            if key:
                request[key] = "dev"
        return request


def _declaration(entry: WorkflowEntry, unit: str) -> Any:
    found = entry.units[unit]
    if isinstance(found, AllDeclaration | ChoiceNode | LeafDeclaration):
        return found
    return found.declare()  # type: ignore[attr-defined]


def _has_choice(entry: WorkflowEntry) -> bool:
    return any(isinstance(_declaration(entry, unit), ChoiceNode) for unit in entry.units)


def _load(path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(f"tree_plan_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _all(directory: Path) -> Iterator[Plan]:
    for path in sorted(directory.glob("*.py")):
        if path.name in NOT_FIXTURES:
            continue
        module = _load(path)
        label = getattr(module, "LABEL", None)
        if label is not None and label.get("expect") == "valid":
            yield Plan(path.stem, module.ENTRY, dict(label), "fixture")
    for tree in generators.GENERATED:
        yield Plan(tree.name, tree.entry, dict(tree.label), "generated")


def valid_plans(kind: str, directory: Path | None = None) -> list[Plan]:
    """Every valid plan of `kind` (`nonchoice` or `choice`), fixtures first (by file name), then
    the generated trees in `GENERATED` order."""
    assert kind in KINDS, kind
    return [plan for plan in _all(directory or FIXTURES) if plan.kind == kind]
