"""L.NW-2.4: a family's cases are ONE suite for every implementation (WR-PROOF-4, SA-14).

Nothing in a `*_cases.py` module may branch on which implementation it is run against: no skip, no
xfail, no conditional on the implementation's name, type or class, and no other `if` on the
factory's `extras` (a fixture-contract value a case needs is required of every implementation, so
the case reads it unconditionally). A case that skipped for one binding would make "the same suite
passes fake and real" mean nothing.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

CASES = sorted(Path(__file__).resolve().parent.glob("*_cases.py"))
BINDING_WORDS = ("fake", "real", "shim")
SKIP_NAMES = {"skip", "skipif", "xfail", "importorskip", "fail"}


def violations(source: str, filename: str = "<cases>") -> list[str]:
    """Why a cases module branches on the binding, if it does (empty when it does not)."""
    found: list[str] = []
    tree = ast.parse(source, filename)
    for node in ast.walk(tree):
        where = f"{filename}:{getattr(node, 'lineno', 0)}"
        if isinstance(node, ast.Attribute) and node.attr in SKIP_NAMES:
            found.append(f"{where}: {node.attr}")
        if isinstance(node, ast.Name) and node.id in SKIP_NAMES:
            found.append(f"{where}: {node.id}")
        if isinstance(node, ast.Attribute) and node.attr == "mark":
            found.append(f"{where}: pytest mark")
        if isinstance(node, (ast.If, ast.IfExp, ast.While)):
            found.extend(_binding_tests(node.test, where))
        if isinstance(node, ast.comprehension):
            for cond in node.ifs:
                found.extend(_binding_tests(cond, where))
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "get":
            base = ast.unparse(node.func.value)
            if base.endswith("extras"):
                found.append(f"{where}: optional extras (read the fixture contract directly)")
    return found


def _binding_tests(test: ast.expr, where: str) -> list[str]:
    text = ast.unparse(test)
    if "extras" in text or ".name" in text or "built.impl" in text or "impl_" in text:
        return [f"{where}: conditional on the implementation: {text}"]
    for node in ast.walk(test):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if any(word in node.value.lower() for word in BINDING_WORDS):
                return [f"{where}: conditional on a binding name: {text}"]
    return []


def test_there_are_case_modules() -> None:
    assert {p.name for p in CASES} >= {"container_cases.py", "compose_cases.py"}


@pytest.mark.parametrize("path", CASES, ids=lambda p: p.name)
def test_family_cases_have_no_impl_branches(path: Path) -> None:
    assert violations(path.read_text(encoding="utf-8"), path.name) == []


@pytest.mark.parametrize(
    "planted",
    [
        "def case(built):\n    if built.name == 'real-docker':\n        return\n",
        "import pytest\n\ndef case(built):\n    pytest.skip('no docker')\n",
        "def case(built):\n    if isinstance(built.impl, Fake):\n        return\n",
        "def case(built):\n    x = built.extras.get('maybe')\n",
        "import pytest\n\n@pytest.mark.xfail\ndef case(built):\n    pass\n",
        "def case(built):\n    if built.extras['flag']:\n        return\n",
    ],
)
def test_the_detector_catches_planted_branches(planted: str) -> None:
    assert violations(planted) != []


def test_a_plain_case_is_clean() -> None:
    plain = "def case(built):\n    for x in built.extras['lifetimes']:\n        assert x\n"
    assert violations(plain) == []
