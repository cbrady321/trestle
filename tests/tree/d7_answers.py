"""The product's tree answers for `differ d7` (L.TR-4.5; MC-06 mode d7 is L.TR-4.7's).

`answer_of(tree_name)` is d7's default answer source (`tests.tree.d7_answers:answer_of`): it
runs the named tree, a base fixture (`two_branch_barrier`) or a generated variant (`permute_3`,
`two_branch_barrier__aaa_wrap`), through the real loop in-library (MC-26's rig: the real lane and
services under a manual clock, every leaf a scripted unit over the tree's own declaration) with one
trigger, and returns the host answer's root class and primary path (B4-C1, the one place the key
runs, B4-I4): `{"class": <outcome>, "primary": [path segment, ...]}`.

The scenario is the plan's "single-trigger tree" (WR-UNIT-7): exactly one leaf ends in a condition
(`failed`); every other leaf that can start is satisfied and a leaf that needs the trigger is never
started (OQ-33). The trigger is a leaf *unit* named by the caller or, by default, a function of the
tree's base fixture alone: the node `generators.WRAPPINGS` wraps in that fixture, else the leaf unit
that sorts first. A tree, its permutations and its wrappings therefore break the same logical node,
and the primary must be that node's path however the tree is declared or wrapped (B4-C4's ordinal
property). The path a wrapped node answers under has the wrapper's segment in it; d7 removes it
before comparing."""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from tests.fixtures.trees import generators
from tests.tree import treekit as tk
from trestle.workflow.declarations import AllDeclaration, WorkflowEntry
from trestle.workflow.units import Failed, Step

BROKE = "fixture.broke"
BROKE_DETAIL = "the fixture's one trigger"


def tree_named(name: str) -> generators.Tree:
    """A base fixture or a generated tree by name; a `KeyError` when there is neither."""
    for tree in generators.GENERATED:
        if tree.name == name:
            return tree
    if (generators.FIXTURES / f"{name}.py").is_file():
        return generators.fixture_tree(name)
    raise KeyError(name)


def leaf_units(entry: WorkflowEntry) -> list[str]:
    """The leaf units of the tree, sorted by name."""
    return sorted(
        name
        for name, unit in entry.units.items()
        if not isinstance(unit, AllDeclaration) and hasattr(unit, "declare")
    )


def base_of(name: str) -> str:
    """The base fixture of a base, a permutation (`permute_<seed>`) or a wrapping
    (`<base>__<wrapper>`)."""
    if name.startswith("permute_"):
        return generators.PERMUTABLE[
            int(name.removeprefix("permute_")) % len(generators.PERMUTABLE)
        ]
    return name.partition("__")[0]


def trigger_of(name: str, entry: WorkflowEntry) -> str:
    """The default trigger of tree `name`: the node its base fixture is wrapped at, else the leaf
    unit that sorts first."""
    base = base_of(name)
    for fixture, node, _wrapper in generators.WRAPPINGS:
        if fixture == base and node in entry.units:
            return node
    return leaf_units(entry)[0]


def _breaks(unit: Any, params: Any, state: Any, effects: Any, ctx: Any) -> Step:
    return Failed(BROKE, BROKE_DETAIL)


def run_rig(entry: WorkflowEntry, trigger: str, directory: Path) -> tk.TreeRig:
    """Run `entry` in `directory` with `trigger` the one leaf that fails; the finished rig."""
    behaviour = {
        trigger: tk.leaf_unit(trigger, advance=_breaks, declaration=_declared(entry, trigger))
    }
    rig = tk.rig_of_entry(directory, entry, behaviour=behaviour)
    rig.run()
    return rig


def run(entry: WorkflowEntry, trigger: str, directory: Path) -> Any:
    """`run_rig`'s host answer (B4-C1) over the lane the loop wrote."""
    return tk.answer_of(run_rig(entry, trigger, directory))


def _declared(entry: WorkflowEntry, unit: str) -> Any:
    return entry.units[unit].declare()  # type: ignore[attr-defined]


def answer_for(entry: WorkflowEntry, trigger: str) -> Any:
    """`run` in a throwaway directory."""
    with tempfile.TemporaryDirectory(prefix="d7-answers-") as scratch:
        return run(entry, trigger, Path(scratch))


def wire(answer: Any) -> dict[str, Any]:
    """The two decisive fields d7 judges, as d7 reads them."""
    return {"class": answer.outcome.value, "primary": list(answer.primary.path)}


def answer_of(tree_name: str, trigger: str | None = None) -> dict[str, Any]:
    """d7's answer source: the answer of `tree_name` with one trigger (default: `trigger_of`)."""
    entry = tree_named(tree_name).entry
    return wire(answer_for(entry, trigger or trigger_of(tree_name, entry)))


def names() -> Iterator[str]:
    """Every tree name d7 asks for: the bases and every variant."""
    from tests.proof.differ_modes import d7_permutation_depth as d7

    seen: set[str] = set()
    for variant in d7.variants():
        for name in (variant.base, variant.name):
            if name not in seen:
                seen.add(name)
                yield name
