"""SA-15 drift proof: `d2_driver.py` imports only the frozen S0 product
surfaces this leaf names, never a `tests.proof.*` helper that may not
exist (or may differ) in an older reader worktree (L.P0-0c.7)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

D2_DRIVER_PATH = Path(__file__).resolve().parents[1] / "d2_driver.py"

ALLOWED_STDLIB = {"__future__", "argparse", "json", "shutil", "sys", "tempfile", "pathlib"}
ALLOWED_PRODUCT = {
    "trestle",
    "trestle.server.ledger",
    "trestle.server.recovery",
    "trestle.server.project",
    "trestle.server.main",
}


def _imported_modules(source: str) -> set[str]:
    tree = ast.parse(source)
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


@pytest.mark.parametrize("sa", ["SA-15"])
def test_d2_driver_imports_only_s0_surfaces(sa: str) -> None:
    modules = _imported_modules(D2_DRIVER_PATH.read_text())
    disallowed = modules - ALLOWED_STDLIB - ALLOWED_PRODUCT
    assert not disallowed, f"d2_driver.py imports disallowed module(s): {disallowed}"
    for module in modules:
        assert not module.startswith("tests.proof"), f"d2_driver.py must not import {module}"
