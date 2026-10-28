"""L.SV-5.17: the loop's library modules branch on no resource kind (V-6.1, V-6.2, B1-I1).

An AST scan, not a live import. `SCOPE` is the four library modules the loop is made of:
`decide.py`, `join.py`, `loop.py` and `facets.py` (L.SV-5.13); the scan fails when any of the four
is missing, so a rename or a move cannot silently shrink it.
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

# The library modules scanned (L.SV-5.13 widened the two of L.SV-5.17 to all four).
SCOPE = ("decide.py", "join.py", "loop.py", "facets.py")

# The one place a facet class is compared: B1-C6 step (1) checks the facet class the unit asked for
# (`create`, `owned`, `safe_start`, `event`) against the class the node declared for that effect.
# It is a check of the unit's own request, not a branch on a resource kind, and it is exempted by
# module and function name only: any other comparison in facets.py is still scanned.
EXEMPT_FUNCTIONS = {"facets.py": frozenset({"_is_requested_class"})}

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


def _exempt_nodes(tree: ast.AST, name: str) -> set[int]:
    """Ids of every node inside a function `EXEMPT_FUNCTIONS` names for module `name`."""
    exempt = EXEMPT_FUNCTIONS.get(name, frozenset())
    return {
        id(inner)
        for func in ast.walk(tree)
        if isinstance(func, ast.FunctionDef | ast.AsyncFunctionDef) and func.name in exempt
        for inner in ast.walk(func)
    }


def scan_source(source: str, name: str) -> list[str]:
    """Every branch in `source` that tests a resource kind (comparison, `in`, `match`)."""
    problems: list[str] = []
    tree = ast.parse(source)
    skipped = _exempt_nodes(tree, name)
    for node in ast.walk(tree):
        if id(node) in skipped:
            continue
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
    """decide reads only LoopFlags; the join, the loop and the facets read no kind."""
    assert set(SCOPE) == {"decide.py", "join.py", "loop.py", "facets.py"}, SCOPE
    for module in SCOPE:
        assert (PACKAGE / module).is_file(), f"{module} is missing from the scanned scope"
    problems: list[str] = []
    for module in SCOPE:
        problems.extend(scan_source((PACKAGE / module).read_text(), module))
    assert problems == [], problems


def test_planted_kind_branch_is_caught() -> None:
    """A planted `if kind ==` in any scanned module is caught; the real source and benign code are
    not; the exemption covers its one named function and nothing else in facets.py."""
    planted_tail = (
        "\n\ndef _planted(kind: str) -> bool:\n"
        '    if kind == "docker_service":\n'
        "        return True\n"
        "    return False\n"
    )
    for module in SCOPE:
        real = (PACKAGE / module).read_text()
        assert scan_source(real, module) == [], module
        hits = scan_source(real + planted_tail, module)
        assert len(hits) == 1 and "branch on" in hits[0], (module, hits)
    # the exempt function is exempt; the same comparison anywhere else in facets.py is not
    facets = (PACKAGE / "facets.py").read_text()
    moved = facets.replace("def _is_requested_class", "def _is_requested_klass")
    assert any("attribute .facet" in hit for hit in scan_source(moved, "facets.py"))
    assert scan_source("def _is_requested_class(d):\n    return d.facet is 1\n", "facets.py") == []
    assert scan_source("def _is_requested_class(d):\n    return d.facet is 1\n", "loop.py")
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
