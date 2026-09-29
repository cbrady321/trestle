"""L.SV-5.17: the loop's library modules branch on no resource kind (V-6.1, V-6.2, B1-I1).

An AST scan, not a live import. The modules present at this leaf are `decide.py` and `join.py`;
L.SV-5.13 adds `loop.py` and `facets.py` to `SCOPE` (and fails when any of the four is missing).
A branch on a resource kind is a comparison, a membership test or a `match` whose operands are a
resource-kind carrier: a bare name or attribute of the kind fields (`kind`, `resource_kind`,
`realization`, `vantage`, `lifetime`, `facet`, `may_touch`, `reachable_from`), or a value of one of
the kind enums (`RealizationKind`, `Vantage`, `Lifetime`, `HostScopeRef`, `EffectFacetClass`)
named literally or as a string equal to one of their values. `StepKind` is a step's class, not a
resource kind, and is exempt.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from trestle.workflow import declarations

pytestmark = pytest.mark.spine

ROOT = Path(__file__).resolve().parents[3]
PACKAGE = ROOT / "trestle" / "workflow"

# The library modules scanned. Present here: decide, join. L.SV-5.13 adds loop and facets.
SCOPE = ("decide.py", "join.py")

KIND_CARRIERS = frozenset(
    {
        "kind",
        "resource_kind",
        "realization_kind",
        "port_kind",
        "realization",
        "vantage",
        "lifetime",
        "facet",
        "may_touch",
        "reachable_from",
    }
)
KIND_ENUMS = {
    "RealizationKind": declarations.RealizationKind,
    "Vantage": declarations.Vantage,
    "Lifetime": declarations.Lifetime,
    "HostScopeRef": declarations.HostScopeRef,
    "EffectFacetClass": declarations.EffectFacetClass,
}
KIND_LITERALS = frozenset(member.value for enum in KIND_ENUMS.values() for member in enum)


def _mentions_kind(node: ast.AST) -> str | None:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and sub.id in KIND_CARRIERS:
            return f"name {sub.id!r}"
        if isinstance(sub, ast.Attribute):
            if sub.attr in KIND_CARRIERS and sub.attr != "kind":
                return f"attribute .{sub.attr}"
            if isinstance(sub.value, ast.Name) and sub.value.id in KIND_ENUMS:
                return f"enum value {sub.value.id}.{sub.attr}"
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            if sub.value in KIND_LITERALS:
                return f"literal {sub.value!r}"
    return None


def scan_source(source: str, name: str) -> list[str]:
    """Every branch in `source` that tests a resource kind (comparison, `in`, `match`)."""
    problems: list[str] = []
    for node in ast.walk(ast.parse(source)):
        tested: list[ast.AST] = []
        if isinstance(node, ast.Compare):
            tested.append(node)
        elif isinstance(node, ast.Match):
            tested.append(node.subject)
            tested.extend(case.pattern for case in node.cases)
        for part in tested:
            found = _mentions_kind(part)
            if found is not None:
                problems.append(f"{name}:{getattr(part, 'lineno', '?')}: branch on {found}")
    return problems


def test_no_kind_branch_outside_decide() -> None:
    """decide reads only LoopFlags; the join and (later) the loop and facets read no kind."""
    for module in SCOPE:
        assert (PACKAGE / module).is_file(), f"{module} is missing from the scanned scope"
    problems: list[str] = []
    for module in SCOPE:
        problems.extend(scan_source((PACKAGE / module).read_text(), module))
    assert problems == [], problems


def test_planted_kind_branch_is_caught() -> None:
    """A planted `if kind ==` in join.py is caught; the real source and benign code are not."""
    real = (PACKAGE / "join.py").read_text()
    planted = real + (
        "\n\ndef _planted(kind: str) -> bool:\n"
        '    if kind == "docker_service":\n'
        "        return True\n"
        "    return False\n"
    )
    assert scan_source(real, "join.py") == []
    hits = scan_source(planted, "join.py")
    assert len(hits) == 1 and "branch on" in hits[0]
    for snippet in (
        "if unit.resource_kind == 'x': pass",
        "if lifetime is Lifetime.RUN: pass",
        "x = 1 if decl.vantage in reachable else 2",
        "match kind:\n    case 'docker_service':\n        pass",
        "if fact.subject == HostScopeRef.DEMO_CREDENTIAL: pass",
        "if backend == 'agent_launched_project': pass",
    ):
        assert scan_source(snippet, "planted.py"), snippet
    for benign in (
        "if step.kind is StepKind.BLOCKED: pass",
        "if flags.repeat is Repeat.SAFE: pass",
        "if a.effect == b.effect: pass",
        "if ref == subject: pass",
    ):
        assert scan_source(benign, "benign.py") == [], benign
