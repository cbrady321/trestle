"""Deterministic tree generators over the MC-B3-01 fixtures (L.TR-0.6).

Three generators, each a pure function of its arguments (no clock, no I/O beyond reading a fixture
file, no unseeded randomness), so the same call gives the same bytes in every process:

* `permute(seed)`: a fixture with the child order of every composite shuffled by `seed`;
* `wrap_deeper(tree, node, name)`: `node` wrapped in a new single-child composite called `name`,
  one level deeper, the ancestors' budgets raised to carry it;
* `hundred_node(n)`: one root `AllDeclaration` with the fixed concurrency bound
  `HUNDRED_NODE_CONCURRENCY` over `n` sibling leaves of one declaration (identical flags, no `needs`
  among them), so the bound is smaller than the sibling count of both the 3- and the 100-sibling
  tree and both queue siblings behind it (A2c6-3).

A generated tree is a `Tree`: a single-file plugin source that satisfies MC-B3-01's import rule
(`trestle.plugin`, `trestle.workflow` and the standard library only) and its label. `GENERATED` is
the only source of generated trees any tree test admits (L.TR-4.5, L.TR-4.7, L.TR-L.10, L.TR-6.4)
and of `valid_plans` (L.TR-3.7); `SCALE_ONLY` names the one generated tree the termination
fault-schedule product leaves out and the tree it reduces to (L.TR-3.7, A2c5-2).

This module is test infrastructure and may import anything; only the plugin source it emits is
held to the fixture import rule."""

from __future__ import annotations

import dataclasses
import importlib.util
import math
import random
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from enum import Enum
from pathlib import Path
from types import ModuleType
from typing import Any

from trestle.common.plan.declared import DeclaredTree
from trestle.workflow import (
    AllDeclaration,
    ChildBinding,
    ChoiceNode,
    CompletionSource,
    Compose,
    LeafDeclaration,
    LoopFlags,
    Repeat,
    WaitPolicy,
    WorkflowEntry,
)
from trestle.workflow.extract import extract_declared_tree

FIXTURES = Path(__file__).resolve().parent

# The published finalization reserve every parent holds back from a child (`clock.
# FINALIZATION_RESERVE_S`, B2-C2 (4)); a wrapper and its ancestors grow by at least this much.
RESERVE_S = 10

# The one declared concurrency bound of every `hundred_node` root (A2c6-3): B = 2 < 3 siblings.
HUNDRED_NODE_CONCURRENCY = 2
HUNDRED_NODE_LEAF_BUDGET_S = 12  # >= the leaf's own wait (10 s), so the leaf registers
HUNDRED_NODE_MAX = 100
# One root budget for every n <= HUNDRED_NODE_MAX, so hundred_node(100) and hundred_node(3) differ
# only in the root's child list: ceil(100 / B) * leaf budget + the reserve.
HUNDRED_NODE_ROOT_BUDGET_S = (
    math.ceil(HUNDRED_NODE_MAX / HUNDRED_NODE_CONCURRENCY) * HUNDRED_NODE_LEAF_BUDGET_S + RESERVE_S
)
HUNDRED_NODE_DEADLINE_S = HUNDRED_NODE_ROOT_BUDGET_S + 3 * RESERVE_S

# The fixed inputs of `GENERATED`.
PERMUTABLE = ("two_branch_barrier", "race_two_trigger", "three_level")
PERMUTE_SEEDS = (1, 2, 3, 4, 5, 6)
# (fixture, node to wrap, wrapper name): every fixture here has depth 2, so each result has depth
# 3; the names sort before and after their siblings on purpose (depth-invariance under any name,
# WR-UNIT-7).
WRAPPINGS = (
    ("two_branch_barrier", "left", "aaa_wrap"),
    ("race_two_trigger", "right", "zzz_wrap"),
    ("three_step_retry_remedy", "deploy", "mid_wrap"),
    ("root_eligible_both", "work", "layer"),
)

_STRUCTURAL = "structural fixture (MC-B3-01): declaration only, no behaviour"

_UNIT_CLASS = '''

class Unit:
    """A declaration-only work unit: a generated tree carries no behaviour (MC-B3-01)."""

    def __init__(self, declaration: LeafDeclaration) -> None:
        self._declaration = declaration

    def declare(self) -> LeafDeclaration:
        return self._declaration

    def observe(self, params: Any, reads: Any, ctx: Any) -> Any:
        raise NotImplementedError(STRUCTURAL)

    def advance(self, params: Any, state: Any, effects: Any, ctx: Any) -> Any:
        raise NotImplementedError(STRUCTURAL)

    def release(self, params: Any, handle: Any, effects: Any, ctx: Any) -> Any:
        raise NotImplementedError(STRUCTURAL)
'''


@dataclass(frozen=True)
class Tree:
    """One generated tree: a publishable single-file plugin and its MC-B3-01 label."""

    name: str
    source: str
    vertices: int
    depth: int
    shared: str | None

    @property
    def label(self) -> dict[str, Any]:
        return {
            "vertices": self.vertices,
            "depth": self.depth,
            "shared": self.shared,
            "expect": "valid",
        }

    def write(self, directory: Path) -> Path:
        """The plugin file `<directory>/<name>.py` (one file per tree, named as its function)."""
        path = directory / f"{self.name}.py"
        path.write_text(self.source, encoding="utf-8")
        return path

    def load(self) -> ModuleType:
        """The plugin module (its `ENTRY` and `LABEL`), executed in a fresh namespace."""
        module = ModuleType(f"generated_tree_{self.name}")
        exec(compile(self.source, f"<generated {self.name}>", "exec"), module.__dict__)  # noqa: S102
        return module

    @property
    def entry(self) -> WorkflowEntry:
        entry = self.load().ENTRY
        assert isinstance(entry, WorkflowEntry)
        return entry


# ---- reading a fixture


def _fixture_module(name: str) -> ModuleType:
    path = FIXTURES / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"tree_fixture_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def declaration_of(entry: WorkflowEntry, unit: str) -> Any:
    """A unit's declaration: a composite is its own data, a leaf answers `declare()`."""
    found = entry.units[unit]
    if isinstance(found, AllDeclaration | ChoiceNode | LeafDeclaration):
        return found
    return found.declare()  # type: ignore[attr-defined]


def fixture_tree(name: str) -> Tree:
    """The MC-B3-01 fixture `name` as a `Tree` (re-rendered, so the generators work on one form)."""
    return _tree(name, _fixture_module(name).ENTRY)


def _bindings(entry: WorkflowEntry, unit: str) -> list[tuple[str, str]]:
    """(logical name, unit) of every child a unit's declaration binds, in declaration order."""
    decl = declaration_of(entry, unit)
    if isinstance(decl, AllDeclaration):
        return [(c.name if c.name is not None else c.unit, c.unit) for c in decl.children]
    if isinstance(decl, ChoiceNode):
        return [(alt.unit, alt.unit) for alt in decl.choice.alternatives]
    return []


def measure(entry: WorkflowEntry) -> tuple[int, int, str | None]:
    """(vertices, depth, shared) as MC-B3-01 labels them, by inspection of the declaration: the
    number of distinct logical nodes referenced (a node is its logical name, so a node two parents
    reference counts once, and an alternative of a choice and an unresolvable reference count too),
    the longest containment chain (a leaf is depth 1), and the one node two different parents
    reference (or None)."""
    parents: dict[str, set[str]] = {}
    depth_of: dict[str, int] = {}

    def walk(name: str, unit: str) -> int:
        if name in depth_of:
            return depth_of[name]
        depth_of[name] = 1  # provisional: a containment cycle would end here
        below = 0
        for child_name, child_unit in _bindings(entry, unit) if unit in entry.units else []:
            parents.setdefault(child_name, set()).add(name)
            below = max(below, walk(child_name, child_unit))
        depth_of[name] = 1 + below
        return depth_of[name]

    depth = walk(entry.root, entry.root)
    shared = sorted(name for name, above in parents.items() if len(above) > 1)
    assert len(shared) <= 1, shared
    return len(depth_of), depth, (shared[0] if shared else None)


def declared_of(entry: WorkflowEntry) -> DeclaredTree:
    """The declared tree (MC-34) of `entry`, descendants resolved: exactly what the validator
    records (L.TR-0.2), so a test can compile a tree without publishing it."""
    return extract_declared_tree(entry)


# ---- rendering a WorkflowEntry back to plugin source


def _render(value: Any, used: set[str]) -> str:
    if isinstance(value, Enum):
        used.add(type(value).__name__)
        return f"{type(value).__name__}.{value.name}"
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        used.add(type(value).__name__)
        args = ", ".join(
            f"{f.name}={_render(getattr(value, f.name), used)}"
            for f in dataclasses.fields(value)
            if f.init
        )
        return f"{type(value).__name__}({args})"
    if isinstance(value, timedelta):
        seconds = value.total_seconds()
        return f"timedelta(seconds={int(seconds) if seconds == int(seconds) else seconds!r})"
    if isinstance(value, frozenset | set):
        items = ", ".join(sorted(_render(v, used) for v in value))
        return f"frozenset({{{items}}})" if items else "frozenset()"
    if isinstance(value, tuple | list):
        items = ", ".join(_render(v, used) for v in value)
        return f"({items},)" if len(value) == 1 else f"({items})"
    if isinstance(value, Mapping):
        return (
            "{"
            + ", ".join(f"{_render(k, used)}: {_render(v, used)}" for k, v in value.items())
            + "}"
        )
    return repr(value)


def _tree(name: str, entry: WorkflowEntry) -> Tree:
    """Render `entry` as the single-file plugin `name` (a `@trestle` function of that name)."""
    used: set[str] = {"WorkflowEntry", "LeafDeclaration"}
    units = []
    for unit in entry.units:
        decl = declaration_of(entry, unit)
        rendered = _render(decl, used)
        if isinstance(decl, LeafDeclaration):
            rendered = f"Unit({rendered})"
        units.append(f"        {unit!r}: {rendered},")
    vertices, depth, shared = measure(entry)
    deadline_s = int(entry.deadline.total_seconds())
    imports = "\n".join(f"    {n}," for n in sorted(used))
    label = {"vertices": vertices, "depth": depth, "shared": shared, "expect": "valid"}
    source = (
        f'"""Generated tree {name} (L.TR-0.6; MC-B3-01): a transformation of a fixture."""\n\n'
        "from __future__ import annotations\n\n"
        "from datetime import timedelta\n"
        "from typing import Any\n\n"
        "from trestle.plugin import Context, trestle\n"
        f"from trestle.workflow import (\n{imports}\n)\n\n"
        f"STRUCTURAL = {_STRUCTURAL!r}\n\n"
        f"LABEL = {label!r}\n"
        f"{_UNIT_CLASS}\n"
        f"ENTRY = WorkflowEntry(\n    root={entry.root!r},\n    units={{\n"
        + "\n".join(units)
        + "\n    },\n"
        f"    deadline=timedelta(seconds={deadline_s}),\n)\n\n\n"
        f"@trestle(deadline={deadline_s})\n"
        f"def {name}(ctx: Context) -> dict[str, str]:\n"
        f'    return {{"fixture": "{name}"}}\n'
    )
    return Tree(name, source, vertices, depth, shared)


# ---- the generators


def _fit(entry: WorkflowEntry) -> WorkflowEntry:
    """`entry` with every `AllDeclaration`'s budget raised to what B2-C2 (4) asks of it: each child
    at most the parent less the reserve, and at least the longest `needs` chain and
    ceil(width / concurrency) * the largest child (children first, so a raise carries upward), and
    the entry deadline to hold the root's budget with room for finalization. A budget only grows."""
    units = dict(entry.units)
    budgets: dict[str, float] = {}

    def budget_of(unit: str) -> float:
        if unit not in budgets:
            decl = declaration_of(entry, unit)
            if isinstance(decl, AllDeclaration):
                kids = [(c.name if c.name is not None else c.unit, c) for c in decl.children]
                weight = {name: budget_of(c.unit) for name, c in kids if c.unit in entry.units}
                largest = max(weight.values(), default=0.0)
                chain: dict[str, float] = {}

                def longest(name: str) -> float:
                    if name not in chain:
                        after = [n for n, c in kids if name in c.needs]
                        chain[name] = weight[name] + max((longest(n) for n in after), default=0.0)
                    return chain[name]

                need = max(
                    max((longest(n) for n in weight), default=0.0),
                    math.ceil(len(kids) / max(decl.concurrency, 1)) * largest,
                    largest + RESERVE_S if largest else 0.0,
                )
                declared = decl.budget.total_seconds() if decl.budget is not None else 0.0
                fitted = max(declared, need)
                if fitted != declared:
                    units[unit] = dataclasses.replace(decl, budget=timedelta(seconds=fitted))
                budgets[unit] = fitted
            else:
                budget = getattr(decl, "budget", None)
                budgets[unit] = budget.total_seconds() if budget is not None else 0.0
        return budgets[unit]

    root_budget = budget_of(entry.root)
    deadline = max(entry.deadline.total_seconds(), root_budget + 3 * RESERVE_S)
    return dataclasses.replace(entry, units=units, deadline=timedelta(seconds=deadline))


def permute(seed: int) -> Tree:
    """A fixture (`PERMUTABLE[seed % 3]`) with every composite's child order shuffled by `seed`.
    `needs` name their siblings, so the tree is the same tree in another declaration order."""
    base = PERMUTABLE[seed % len(PERMUTABLE)]
    entry = _fixture_module(base).ENTRY
    rng = random.Random(seed)
    units: dict[str, Any] = {}
    for unit in sorted(entry.units):
        obj = entry.units[unit]
        decl = declaration_of(entry, unit)
        if isinstance(decl, AllDeclaration) and len(decl.children) > 1:
            shuffled = list(decl.children)
            rng.shuffle(shuffled)
            obj = dataclasses.replace(decl, children=tuple(shuffled))
        units[unit] = obj
    ordered = {unit: units[unit] for unit in entry.units}
    return _tree(f"permute_{seed}", dataclasses.replace(entry, units=ordered))


def wrap_deeper(tree: Tree, node: str, name: str) -> Tree:
    """`tree` with `node` wrapped in a new single-child composite `name`: the one binding that
    names `node` now names `name`, keeping its `needs`, and `name` runs `node` alone. Budgets are
    refitted (`_fit`), so the carve still fits the deeper tree."""
    entry = tree.entry
    referencing = [
        (unit, child)
        for unit in entry.units
        if isinstance(decl := declaration_of(entry, unit), AllDeclaration)
        for child in decl.children
        if child.unit == node
    ]
    if len(referencing) != 1:
        raise ValueError(f"{node!r} must be bound exactly once, found {len(referencing)}")
    if name in entry.units:
        raise ValueError(f"{name!r} is already a unit")
    parent, binding = referencing[0]
    node_budget = declaration_of(entry, node).budget
    assert node_budget is not None
    old_name = binding.name if binding.name is not None else binding.unit

    units: dict[str, Any] = dict(entry.units)
    outer = declaration_of(entry, parent)
    # the outer binding is the wrapper (its name is now the logical name the siblings' `needs`
    # and the composite's `gates` mean); `node` moves inside it
    children = tuple(
        dataclasses.replace(c, unit=name, name=None, params={}) if c is binding else c
        for c in outer.children
    )
    children = tuple(
        dataclasses.replace(c, needs=tuple(name if n == old_name else n for n in c.needs))
        for c in children
    )
    units[parent] = dataclasses.replace(
        outer,
        children=children,
        gates=tuple(name if g == old_name else g for g in outer.gates),
    )
    units[name] = AllDeclaration(
        unit=name,
        flags=LoopFlags(Compose.ALL, CompletionSource.OBSERVED, Repeat.SAFE),
        children=(
            ChildBinding(
                unit=node,
                params=binding.params,
                needs=(),
                vantage=binding.vantage,
                name=binding.name,
            ),
        ),
        concurrency=1,
        budget=node_budget + timedelta(seconds=RESERVE_S),
        identifier_sets={},
        arg_bindings=(),
        env_key_field=None,
    )
    return _tree(f"{tree.name}__{name}", _fit(dataclasses.replace(entry, units=units)))


def hundred_node(n: int) -> Tree:
    """One root `AllDeclaration` (V-14) with concurrency `HUNDRED_NODE_CONCURRENCY` over `n`
    sibling leaves of one declaration: identical flags, distinct logical names and parameters, no
    `needs` among them. `n` is at most `HUNDRED_NODE_MAX`, which the shared root budget carves."""
    if not 1 <= n <= HUNDRED_NODE_MAX:
        raise ValueError(f"n must be within 1..{HUNDRED_NODE_MAX}, got {n}")
    leaf = LeafDeclaration(
        unit="node",
        flags=LoopFlags(Compose.LEAF, CompletionSource.OBSERVED, Repeat.SAFE),
        preconditions=(),
        postcondition="ready",
        wait=WaitPolicy(timedelta(seconds=1), 1.0, timedelta(seconds=10)),
        resource_kind="marker",
        may_touch=frozenset({"marker"}),
        effects=(),
        retryable=frozenset(),
        remedies=(),
        budget=timedelta(seconds=HUNDRED_NODE_LEAF_BUDGET_S),
        max_attempts=1,
    )
    root = AllDeclaration(
        unit="hundred",
        flags=LoopFlags(Compose.ALL, CompletionSource.OBSERVED, Repeat.SAFE),
        children=tuple(
            ChildBinding(unit="node", params={"index": i}, needs=(), name=f"n{i:03d}")
            for i in range(n)
        ),
        concurrency=HUNDRED_NODE_CONCURRENCY,
        budget=timedelta(seconds=HUNDRED_NODE_ROOT_BUDGET_S),
        identifier_sets={},
        arg_bindings=(),
        env_key_field=None,
    )
    entry = WorkflowEntry(
        root="hundred",
        units={"hundred": root, "node": leaf},
        deadline=timedelta(seconds=HUNDRED_NODE_DEADLINE_S),
    )
    return _tree(f"hundred_node_{n}", entry)


def _generated() -> tuple[Tree, ...]:
    permuted = tuple(permute(seed) for seed in PERMUTE_SEEDS)
    wrapped = tuple(
        wrap_deeper(fixture_tree(fixture), node, name) for fixture, node, name in WRAPPINGS
    )
    return (*permuted, *wrapped, hundred_node(100), hundred_node(3))


# The only generated trees any tree test admits (A2c4-6, IC5-6): every root a test admits is
# enumerated here or reduced through `SCALE_ONLY` to an enumerated tree by L.TR-3.7's argument.
GENERATED: tuple[Tree, ...] = _generated()

# The one generated tree kept out of the termination fault-schedule product, and the tree it
# reduces to (L.TR-3.7, A2c5-2): same root concurrency B, B < the 3 siblings, same leaf declaration
# and flags, same depth; they differ only in the root's child list.
SCALE_ONLY: dict[Tree, Tree] = {hundred_node(100): hundred_node(3)}
