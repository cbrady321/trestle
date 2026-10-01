"""Design-quality check for phase-local contracts (L.CZ.8; J-ROOT step 7, MC-<PHASE>-nn).

A phase-local contract (`MC-CORE-nn`, `MC-B2-nn`, `MC-B3-nn`, `MC-B-nn`) may not be consumed by
another phase. The phase boundary is the CSC-11 test root, never a fence glob (R2-2): a helper
module under a phase's own roots whose docstring cites one of its phase's contracts is that
contract's module, and every file under *another* phase's roots that imports it is a violation. P0's
court modules (`MC-P0-nn`, and the shared `tests/proof/**` files later phases extend) are the base
every phase reads, so they are not judged, nor is a file outside every phase's roots.
"""

from __future__ import annotations

import ast
import fnmatch
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

# MC id prefix -> the phase that owns it (A-1 = single-level `MC-B2-`, A-2 = tree `MC-B3-`)
PREFIX_PHASE = {"CORE": "core", "B2": "single", "B3": "tree", "B": "b"}
CONTRACT_RE = re.compile(r"\bMC-(CORE|B2|B3|B)-\d+")
# CSC-11's phase-owned test roots (the drift tests of each later phase included)
PHASE_ROOTS: dict[str, list[str]] = {
    "core": ["tests/core/**", "tests/spine/**", "tests/proof/drift/core/**"],
    "single": ["tests/single/**", "tests/proof/drift/single/**"],
    "tree": ["tests/tree/**", "tests/proof/drift/tree/**"],
    "b": [
        "packages/trestle-packs/tests/**",
        "packages/trestle-env/tests/**",
        "tests/proof/b/**",
        "tests/proof/drift/b/**",
    ],
}


# Recorded crossings (L.CZ.8.fix1): slice B builds on A-2's tree runtime, and its lanes reused three
# of the tree phase's test kits without the plan registering them as root contracts (plan gap; B's
# plan file names none of them). Each is (module, the foreign phase allowed to import it); every
# other crossing still fails, and an entry no file uses any more fails too, so the list cannot go
# stale.
# Moving the kits to a root-registered module is the follow-up (a WR-Fix on B and tree paths).
RECORDED_CROSSINGS: frozenset[tuple[str, str]] = frozenset(
    {
        ("tests/tree/treekit.py", "b"),  # MC-B3-01 fixture runner (B's env tests run trees)
        ("tests/tree/hostpath.py", "b"),  # MC-B3-02 host-path runner (B's readiness HOST test)
        ("tests/tree/gen_fossils.py", "b"),  # MC-B3-05 fossil producers (B's fossil states)
    }
)


def phase_of(rel: str, roots: dict[str, list[str]] = PHASE_ROOTS) -> str | None:
    for phase, globs in roots.items():
        if any(fnmatch.fnmatch(rel, g) for g in globs):
            return phase
    return None


def _py_files(repo: Path) -> dict[str, ast.Module]:
    found: dict[str, ast.Module] = {}
    for base in ("tests", "packages"):
        for path in sorted((repo / base).rglob("*.py")):
            rel = path.relative_to(repo).as_posix()
            if base == "packages" and "/tests/" not in f"/{rel}":
                continue
            try:
                found[rel] = ast.parse(path.read_text(encoding="utf-8"))
            except (OSError, SyntaxError, UnicodeDecodeError):
                continue
    return found


def contract_modules(
    files: dict[str, ast.Module], roots: dict[str, list[str]] = PHASE_ROOTS
) -> dict[str, str]:
    """rel path -> its phase, for each helper module (not a test, conftest or fixture) under a
    phase's roots whose docstring cites a contract of that same phase."""
    modules: dict[str, str] = {}
    for rel, tree in files.items():
        name = Path(rel).name
        if name.startswith("test_") or name == "conftest.py" or "/fixtures/" in f"/{rel}":
            continue
        phase = phase_of(rel, roots)
        if phase is None:
            continue
        cited = {
            PREFIX_PHASE[m.group(1)] for m in CONTRACT_RE.finditer(ast.get_docstring(tree) or "")
        }
        if phase in cited:
            modules[rel] = phase
    return modules


def _dotted_variants(rel: str) -> list[str]:
    parts = list(Path(rel).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return [".".join(parts[i:]) for i in range(len(parts) - 1)]


def cross_phase_imports(
    repo: Path = ROOT,
    roots: dict[str, list[str]] = PHASE_ROOTS,
    recorded: frozenset[tuple[str, str]] = frozenset(),
    used: set[tuple[str, str]] | None = None,
) -> list[str]:
    """Every import of a phase-local contract module from a file under another phase's roots,
    except a `recorded` (module, importer phase) crossing, which is added to `used` instead."""
    files = _py_files(repo)
    modules = contract_modules(files, roots)
    by_name: dict[str, set[str]] = {}
    for rel in modules:
        for dotted in _dotted_variants(rel):
            by_name.setdefault(dotted, set()).add(rel)

    violations: list[str] = []
    for rel, tree in files.items():
        importer = phase_of(rel, roots)
        if importer is None:
            continue
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = [node.module] + [f"{node.module}.{alias.name}" for alias in node.names]
            for name in names:
                for target in sorted(by_name.get(name, ())):
                    if modules[target] == importer:
                        continue
                    if (target, importer) in recorded:
                        if used is not None:
                            used.add((target, importer))
                        continue
                    if True:
                        violations.append(
                            f"{rel}:{node.lineno} ({importer}) imports {target} "
                            f"({modules[target]}'s contract module)"
                        )
    return sorted(set(violations))


def test_no_phase_local_contract_crosses_phases() -> None:
    files = _py_files(ROOT)
    modules = contract_modules(files)
    # the scan is not vacuous: core, tree and B each own contract modules
    assert {"core", "tree", "b"} <= set(modules.values()), sorted(set(modules.values()))
    used: set[tuple[str, str]] = set()
    assert cross_phase_imports(ROOT, recorded=RECORDED_CROSSINGS, used=used) == []
    assert used == set(RECORDED_CROSSINGS), sorted(RECORDED_CROSSINGS - used)


def _plant(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_planted_cross_phase_import_fails(tmp_path: Path) -> None:
    _plant(
        tmp_path,
        "tests/tree/capacity.py",
        '"""The tree capacity helper (MC-B3-10)."""\nLIMIT = 1\n',
    )
    _plant(tmp_path, "tests/tree/test_capacity.py", "from tests.tree import capacity\n")
    # a file of another phase importing it is the planted violation, whichever import form it uses
    _plant(tmp_path, "tests/core/test_uses_tree.py", "from tests.tree.capacity import LIMIT\n")
    _plant(
        tmp_path,
        "packages/trestle-env/tests/unit/test_uses_tree.py",
        "import tests.tree.capacity\n",
    )
    found = cross_phase_imports(tmp_path)
    assert len(found) == 2, found
    assert any(
        v.startswith("tests/core/test_uses_tree.py:1 (core) imports tests/tree/capacity.py")
        for v in found
    )
    assert any(
        v.startswith("packages/trestle-env/tests/unit/test_uses_tree.py:1 (b)") for v in found
    )

    # the phase's own files, a file outside every phase's roots and an unrelated import pass
    clean = tmp_path / "clean"
    _plant(clean, "tests/tree/capacity.py", '"""Helper (MC-B3-10)."""\n')
    _plant(clean, "tests/tree/test_capacity.py", "from tests.tree import capacity\n")
    _plant(clean, "tests/proof/harness.py", "from tests.tree import capacity\n")
    _plant(clean, "tests/core/test_other.py", "import os\n")
    assert cross_phase_imports(clean) == []

    # a helper citing another phase's contract is not that phase's contract module
    other = tmp_path / "other"
    _plant(other, "tests/tree/plans.py", '"""Plan helpers (MC-CORE-04)."""\n')
    _plant(other, "tests/core/test_plans.py", "from tests.tree import plans\n")
    assert cross_phase_imports(other) == []

    # a recorded crossing passes and is reported used; an unrecorded one beside it still fails
    used: set[tuple[str, str]] = set()
    recorded = frozenset({("tests/tree/capacity.py", "b")})
    found = cross_phase_imports(tmp_path, recorded=recorded, used=used)
    assert used == set(recorded)
    assert [v.split(" ")[0] for v in found] == ["tests/core/test_uses_tree.py:1"]
