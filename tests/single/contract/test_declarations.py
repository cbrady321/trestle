"""L.SV-2.1: the declaration data model is pure frozen data, stdlib only, and transcribes
V-14 (V-6 for `LoopFlags`, B1-C8 for `WorkflowEntry`) field for field."""

from __future__ import annotations

import ast
import dataclasses
import datetime
import sys
from pathlib import Path

import pytest

from trestle.workflow import declarations as d

ROOT = Path(__file__).resolve().parents[3]
PACKAGE = ROOT / "trestle" / "workflow"

# Field names in declaration order, exactly as the interfaces write them.
EXPECTED_FIELDS: dict[type, tuple[str, ...]] = {
    d.LoopFlags: ("compose", "completion", "repeat"),
    d.WaitPolicy: ("poll_every", "backoff", "max_wait"),
    d.EffectDeclaration: (
        "effect",
        "facet",
        "verb",
        "lifetime",
        "host_sections",
        "release_timeout",
        "is_release",
    ),
    d.RemedyDeclaration: ("code", "effect", "attempts", "total", "cooldown"),
    d.LeafDeclaration: (
        "unit",
        "flags",
        "preconditions",
        "postcondition",
        "wait",
        "resource_kind",
        "may_touch",
        "effects",
        "retryable",
        "remedies",
        "budget",
        "max_attempts",
        "env_key_field",
    ),
    d.ChildBinding: ("unit", "params", "needs", "vantage", "name"),
    d.ArgBinding: ("arg", "identifier_set", "filters_children"),
    d.AllDeclaration: (
        "unit",
        "flags",
        "children",
        "concurrency",
        "budget",
        "identifier_sets",
        "arg_bindings",
        "env_key_field",
        "gates",
    ),
    d.ChoiceNode: ("unit", "flags", "choice", "budget"),
    d.Alternative: ("unit", "realization", "reachable_from", "human_action"),
    d.ChoiceDeclaration: ("logical_system", "alternatives", "select_arg", "fallback", "readiness"),
    d.WorkflowEntry: ("root", "units", "deadline"),
}

# The WR-PLAN-12 elements, in V-14's order: preconditions, postcondition, wait, resource kind,
# touch scope, and the repeat flag inside `flags` (element 6).
PLAN12_FIELDS = ("preconditions", "postcondition", "wait", "resource_kind", "may_touch")


def _module_sources() -> list[Path]:
    return sorted(PACKAGE.glob("*.py"))


def test_types_frozen_slots_stdlib_only() -> None:
    for cls in EXPECTED_FIELDS:
        params = cls.__dataclass_params__  # type: ignore[attr-defined]
        assert params.frozen, cls.__name__
        assert "__slots__" in vars(cls), cls.__name__
    # frozen means assignment raises
    flags = d.LoopFlags(d.Compose.LEAF, d.CompletionSource.OBSERVED, d.Repeat.SAFE)
    with pytest.raises(dataclasses.FrozenInstanceError):
        flags.compose = d.Compose.ALL  # type: ignore[misc]
    with pytest.raises((AttributeError, TypeError)):
        flags.extra = 1  # type: ignore[attr-defined]

    stdlib = set(sys.stdlib_module_names)
    for path in _module_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            for name in names:
                top = name.split(".")[0]
                allowed = (
                    top in stdlib
                    or name == "trestle.plugin"
                    or name.startswith("trestle.plugin.")
                    or name == "trestle.common.plan"
                    or name.startswith("trestle.common.plan.")
                    or name == "trestle.workflow"
                    or name.startswith("trestle.workflow.")
                )
                assert allowed, f"{path.name} imports {name}"
                assert not name.startswith(
                    (
                        "trestle.server",
                        "trestle.child",
                        "trestle.query",
                        "trestle.ops",
                        "trestle.wrapper",
                    )
                ), f"{path.name} imports {name}"


def test_leaf_declaration_carries_six_plan12_elements() -> None:
    names = tuple(f.name for f in dataclasses.fields(d.LeafDeclaration))
    assert names == EXPECTED_FIELDS[d.LeafDeclaration]
    for element in PLAN12_FIELDS:
        assert element in names
    assert names.index("preconditions") < names.index("postcondition") < names.index("wait")
    # element 6 is the repeat flag, carried by LoopFlags
    assert "flags" in names
    assert tuple(f.name for f in dataclasses.fields(d.LoopFlags)) == (
        "compose",
        "completion",
        "repeat",
    )
    for extra in ("budget", "max_attempts", "retryable", "env_key_field"):
        assert extra in names


def test_composite_types_exist_as_data() -> None:
    for cls, expected in EXPECTED_FIELDS.items():
        assert tuple(f.name for f in dataclasses.fields(cls)) == expected, cls.__name__
    # no methods on the composites: an author writes no orchestration (B1-C5)
    for cls in (d.AllDeclaration, d.ChoiceNode, d.ChildBinding, d.ArgBinding):
        public = [n for n, v in vars(cls).items() if callable(v) and not n.startswith("_")]
        assert public == [], (cls.__name__, public)
    binding = d.ChildBinding(unit="u", params={"k": 1}, needs=())
    assert binding.vantage is d.Vantage.HOST and binding.name is None
    node = d.AllDeclaration(
        unit="root",
        flags=d.LoopFlags(d.Compose.ALL, d.CompletionSource.OBSERVED, d.Repeat.SAFE),
        children=(binding,),
        concurrency=1,
        budget=datetime.timedelta(seconds=5),
        identifier_sets={},
        arg_bindings=(),
        env_key_field=None,
    )
    assert node.gates == ()
