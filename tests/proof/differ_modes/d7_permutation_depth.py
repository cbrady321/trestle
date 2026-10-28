"""`python -m tests.proof.differ d7 [--answers <module:function>]` (MC-06 mode d7; CSC-5 builder
L.TR-4.7).

Permutation and one-level-deeper invariance of the tree roll-up (B4-C4's ordinal property,
WR-UNIT-7): one MC-B3-01 tree keeps its root class and its logical primary path when its
composites' children are declared in another order, and when a node is wrapped one composite
deeper under any wrapper name. The variants are exactly the permutations and wrappings in
`generators.GENERATED` (L.TR-0.6); each is compared with the answer of its own base fixture.

The mode judges answers, it does not compute them: an *answer source* is a callable
`answer_of(tree_name) -> {"class": str, "primary": [path segment, ...] | None}` taking a base
fixture name (`two_branch_barrier`) or a generated variant name (`permute_3`,
`two_branch_barrier__aaa_wrap`). The default source is `tests.tree.d7_answers:answer_of`, the
product's tree answers (L.TR-4.5 supplies it); a planted source lets the mode's own self-test
run before any roll-up exists. Both answers pass the one structural normalizer (SA-07) before
they are compared, and only the root class and the primary path decide.

The *logical* primary path is the answer's path with the wrapper's segment removed: wrapping a
node inserts one physical level, and the invariance claim is that the same logical node is
still the primary.
"""

from __future__ import annotations

import argparse
import importlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from tests.fixtures.trees import generators
from tests.proof import normalize as normalize_mod

DEFAULT_ANSWERS = "tests.tree.d7_answers:answer_of"

AnswerOf = Callable[[str], Mapping[str, Any]]

_PERMUTE_PREFIX = "permute_"
_WRAP_SEPARATOR = "__"


@dataclass(frozen=True)
class Variant:
    """One tree of `GENERATED` that d7 compares with its base fixture's answer."""

    name: str  # the generated tree's name: what the answer source is asked for
    base: str  # the fixture it is a permutation or wrapping of
    kind: str  # "permutation" | "wrapping"
    seed: int | None = None
    wrapper: str | None = None

    def describe(self) -> str:
        if self.kind == "permutation":
            return f"permutation seed {self.seed} ({self.name}, base {self.base})"
        return f"wrapper {self.wrapper!r} ({self.name}, base {self.base})"


def variants(trees: Sequence[generators.Tree] | None = None) -> list[Variant]:
    """The permutations and wrappings among `trees` (default `generators.GENERATED`); the
    scale-only `hundred_node` trees are neither and are left out."""
    out: list[Variant] = []
    for tree in generators.GENERATED if trees is None else trees:
        if tree.name.startswith(_PERMUTE_PREFIX):
            seed = int(tree.name.removeprefix(_PERMUTE_PREFIX))
            base = generators.PERMUTABLE[seed % len(generators.PERMUTABLE)]
            out.append(Variant(tree.name, base, "permutation", seed=seed))
        elif _WRAP_SEPARATOR in tree.name:
            base, _, wrapper = tree.name.partition(_WRAP_SEPARATOR)
            out.append(Variant(tree.name, base, "wrapping", wrapper=wrapper))
    return out


def _decisive(answer: Mapping[str, Any], *, wrapper: str | None = None) -> dict[str, Any]:
    """The two fields d7 judges, put through the SA-07 normalizer; `wrapper`'s segment is
    removed from the primary path."""
    primary = answer.get("primary")
    if primary is not None:
        primary = [seg for seg in primary if seg != wrapper]
    return dict(normalize_mod.normalize({"class": answer.get("class"), "primary": primary}))


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def check(answer_of: AnswerOf, chosen: Sequence[Variant] | None = None) -> list[str]:
    """The differences of every variant from its base fixture's answer (empty: invariant); each
    line names the seed or wrapper. A base whose answer is unavailable is itself a difference."""
    diffs: list[str] = []
    bases: dict[str, dict[str, Any] | None] = {}
    for variant in variants() if chosen is None else chosen:
        if variant.base not in bases:
            try:
                bases[variant.base] = _decisive(answer_of(variant.base))
            except KeyError:
                bases[variant.base] = None
        base = bases[variant.base]
        if base is None:
            diffs.append(f"{variant.describe()}: the base fixture has no answer")
            continue
        try:
            got = _decisive(answer_of(variant.name), wrapper=variant.wrapper)
        except KeyError:
            diffs.append(f"{variant.describe()}: no answer")
            continue
        for key, label in (("class", "root class"), ("primary", "logical primary path")):
            if _canonical(got[key]) != _canonical(base[key]):
                diffs.append(
                    f"{variant.describe()}: {label} {_canonical(got[key])} "
                    f"differs from the base's {_canonical(base[key])}"
                )
    return diffs


def _resolve(spec: str) -> AnswerOf:
    module_name, func_name = spec.split(":")
    return getattr(importlib.import_module(module_name), func_name)  # type: ignore[no-any-return]


def run(answer_of: AnswerOf) -> int:
    chosen = variants()
    diffs = check(answer_of, chosen)
    for diff in diffs:
        print(f"d7: DIFF: {diff}")
    print(f"d7: {len(chosen)} permutations and wrappings checked, {len(diffs)} diffs")
    return 1 if diffs else 0


def main(args: argparse.Namespace) -> int:
    spec = args.answers or DEFAULT_ANSWERS
    try:
        answer_of = _resolve(spec)
    except (ImportError, AttributeError) as exc:
        # Not "not built": the mode is built; the product's tree answers are not there yet.
        print(f"d7: no answer source {spec!r} ({exc}); L.TR-4.5 supplies the product's")
        return 2
    return run(answer_of)
