"""L.TR-1.7: the host-path runner (MC-B3-02) imports no harness internals, and a refused tree
leaves nothing behind."""

from __future__ import annotations

import ast
from pathlib import Path

from tests.tree import hostpath
from tests.tree.test_tr1_admission import descendants, publish, run_dirs, source
from trestle.common import codes
from trestle.common.types import RequestOutcome
from trestle.server.main import Kernel

HOSTPATH = Path(hostpath.__file__)
# MC-26 (`tests/proof/harness.py`) writes an admitted run directly: nothing that bypasses
# admission may be imported by the host-path runner
HARNESS_MODULE = "tests.proof.harness"
HARNESS_NAMES = {"admit_tree", "drive_tree", "run_tree", "AdmittedTree", "fresh_kernel"}


def harness_imports(text: str) -> list[str]:
    """Every import in `text` that reaches `tests/proof/harness.py` or one of its MC-26 names."""
    found: list[str] = []
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.Import):
            found += [a.name for a in node.names if a.name.startswith(HARNESS_MODULE)]
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.startswith(HARNESS_MODULE):
                found.append(module)
            elif module == "tests.proof":
                found += [f"{module}.{a.name}" for a in node.names if a.name == "harness"]
            found += [f"{module}.{a.name}" for a in node.names if a.name in HARNESS_NAMES]
    return found


def test_runner_imports_no_harness_internals() -> None:
    assert harness_imports(HOSTPATH.read_text(encoding="utf-8")) == []
    # the check catches each way of reaching the harness (a planted import per form)
    for planted in (
        "import tests.proof.harness",
        "from tests.proof import harness",
        "from tests.proof.harness import run_tree",
        "from tests.proof import harness as h",
    ):
        assert harness_imports(planted), planted


def test_refused_tree_leaves_no_run_dir(tree_kernel: Kernel) -> None:
    """`misfit` published and run through the runner: a `RequestOutcome` with the misfit code,
    and no run dir, ledger or process afterwards."""
    name = publish(tree_kernel, source("misfit"))
    before = descendants()
    outcome = hostpath.run_tree_via_host(tree_kernel, name)
    assert isinstance(outcome, RequestOutcome), outcome
    assert outcome.code == codes.BUDGET_DOES_NOT_FIT
    assert run_dirs(tree_kernel) == []
    assert not list(tree_kernel.home.glob("runs/**/ledger*"))
    assert descendants() == before
