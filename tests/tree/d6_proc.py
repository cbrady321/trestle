"""Real-process runs for the D6 position proofs (L.TR-6.1 PROC variant, L.TR-6.2 host twin).

A unit is published twice, as its own root plugin (`direct`) and as a child of a two-level parent
(`child`), and each is run through a kernel, the wrapper and a child process. The units are the same
class over the same fakes (`FakeMarker`, one logical system per node); only the plugin's tree
differs.

* `plugin_source(name, shape)`: the plain pair, `work` alone as a root, or `both` (`prep`, then
  `work` needing `prep`), `root_eligible_both`'s shape with real behaviour;
* `exception_sources()`: the after-stop pair. The child is `exception_branch` (a sibling raises
  while `sibling_a` and `sibling_b` are mid-wait, L.TR-3.6), the direct is the same fixture source
  with `sibling_a` as the root and nothing else of the tree reachable: the same unit, over the same
  behaviour, never stopped."""

from __future__ import annotations

from tests.tree import treekit as tk

DIRECT = "d6_direct"
CHILD = "d6_child"
EXCEPTION_DIRECT = "d6_exception_direct"
EXCEPTION_FIXTURE = "exception_branch"
EXCEPTION_CHILD = "d6_exception_child"
STOPPED_NODE = "sibling_a"

_HEADER = '''\
from __future__ import annotations

from datetime import timedelta
from typing import Any

from trestle_packs.fakes import FakeMarker

from trestle.plugin import Context, trestle
from trestle.workflow import (
    AllDeclaration,
    ChildBinding,
    CompletionSource,
    Compose,
    EffectDeclaration,
    EffectFacetClass,
    LeafDeclaration,
    Lifetime,
    LoopFlags,
    RealizationKind,
    Repeat,
    WaitPolicy,
    WorkflowEntry,
)
from trestle.workflow.loop import run_tree
from trestle.workflow.ports import ResourceCreate, ResourceOwned, ResourceReads, ResourceSpec
from trestle.workflow.units import ActContext, Acted, EffectFacets, ObserveContext, ReadFacets, Step
from trestle.workflow.values import CheckResult, CreatedHandle, FoundRef, Observation, Verdict

CREATE_EFFECT = "up"
STOP_EFFECT = "stop"


class Node:
    """A unit that creates its marker and is done once it is present; `release` stops it. The same
    class, declaration and behaviour at the root and inside a parent (B1-I7)."""

    def __init__(self, unit: str) -> None:
        self._unit = unit
        self._spec = ResourceSpec(unit, RealizationKind.AGENT_LAUNCHED_PROJECT, "d6", None)

    def declare(self) -> LeafDeclaration:
        return LeafDeclaration(
            unit=self._unit,
            flags=LoopFlags(Compose.LEAF, CompletionSource.OBSERVED, Repeat.SAFE),
            preconditions=(),
            postcondition="ready",
            wait=WaitPolicy(timedelta(seconds=1), 1.0, timedelta(seconds=6)),
            resource_kind="marker",
            may_touch=frozenset({"marker"}),
            effects=(
                EffectDeclaration(
                    CREATE_EFFECT, EffectFacetClass.CREATE, "", Lifetime.RUN, frozenset(),
                    timedelta(seconds=2),
                ),
                EffectDeclaration(
                    STOP_EFFECT, EffectFacetClass.OWNED, "", Lifetime.RUN, frozenset(),
                    timedelta(seconds=2), is_release=True,
                ),
            ),
            retryable=frozenset(),
            remedies=(),
            budget=timedelta(seconds=10),
            max_attempts=1,
            env_key_field="env",
        )

    def observe(self, params: Any, reads: ReadFacets, ctx: ObserveContext) -> Observation:
        seen = reads.read(ResourceReads).observe(self._spec, ctx.lineage, CREATE_EFFECT)
        return Observation(
            present=seen.selector_present or bool(seen.found),
            selector_present=seen.selector_present,
            identity_proven=seen.identity_proven,
            configuration_compatible=seen.configuration_compatible,
            postcondition=CheckResult(seen.selector_present, None, ""),
            preconditions=(),
            currency=(),
            found=tuple(FoundRef(f.resource_kind, f.selector, f.observed_at) for f in seen.found),
            code=seen.code,
            payload=None,
        )

    def advance(self, params: Any, state: Verdict, effects: EffectFacets, ctx: ActContext) -> Step:
        effects.create(ResourceCreate).create(self._spec, CREATE_EFFECT)
        return Acted()

    def release(self, params: Any, handle: CreatedHandle, effects: Any, ctx: ActContext) -> Step:
        effects.owned(ResourceOwned).stop(handle, STOP_EFFECT)
        return Acted()

'''

_DIRECT = """\
ENTRY = WorkflowEntry(
    root="work",
    units={"work": Node("work")},
    deadline=timedelta(seconds=36),
)
"""

_CHILD = """\
ENTRY = WorkflowEntry(
    root="both",
    units={
        "both": AllDeclaration(
            unit="both",
            flags=LoopFlags(Compose.ALL, CompletionSource.OBSERVED, Repeat.SAFE),
            children=(
                ChildBinding(unit="prep", params={}, needs=()),
                ChildBinding(unit="work", params={}, needs=("prep",)),
            ),
            concurrency=2,
            budget=timedelta(seconds=20),
            identifier_sets={},
            arg_bindings=(),
            env_key_field="env",
        ),
        "prep": Node("prep"),
        "work": Node("work"),
    },
    deadline=timedelta(seconds=36),
)
"""

_FOOTER = """

@trestle(deadline=36, env_arg="env")  # the entry's 36 s (a decorator argument is a literal)
def {name}(ctx: Context, env: str = "dev") -> dict[str, str]:
    marker = FakeMarker(ctx.tmp / "markers", "run")
    ports = {{ResourceReads: marker, ResourceCreate: marker, ResourceOwned: marker}}
    run_tree(ctx, ENTRY, {{"env": env}}, ports=ports)
    return {{"env": env}}
"""


def plugin_source(name: str, shape: str) -> str:
    """The published source of plugin `name`: `shape` `direct` (`work` the root) or `child`."""
    assert shape in ("direct", "child"), shape
    return _HEADER + (_DIRECT if shape == "direct" else _CHILD) + _FOOTER.format(name=name)


def exception_sources() -> tuple[str, str]:
    """`(direct, child)` sources of the after-stop pair (see the module docstring). Both are the
    fixture's source with every `Node` declaring the environment argument (`env_key_field`), so the
    unit's declaration is the same in both positions (B1-I7) and a root plugin that names
    `env_arg` has a root that names it too (WR-OWN-8)."""
    base = tk.fixture_source(EXCEPTION_FIXTURE)
    old = "            max_attempts=1,\n        )"
    new = '            max_attempts=1,\n            env_key_field="env",\n        )'
    assert base.count(old) == 1, f"{EXCEPTION_FIXTURE}: {old!r} moved"
    base = base.replace(old, new)
    assert base.count("def exception_branch(") == 1
    child = base.replace("def exception_branch(", f"def {EXCEPTION_CHILD}(")
    assert child.count('root="app",') == 1
    direct = child.replace("def " + EXCEPTION_CHILD + "(", f"def {EXCEPTION_DIRECT}(").replace(
        'root="app",', f'root="{STOPPED_NODE}",'
    )
    return direct, child
