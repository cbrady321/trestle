"""K-6 scope guard (L.NW-1.1, MJ.NW-1): `runner.py` changes only `StackRunner.down` and
`StackRunner._teardown` against the core checkpoint (BFD-49a; BFD-49 Leave for everything else).

The reference is `git show wr-ckpt/core:<runner.py>` when that tag is reachable (the core
bundle branch stands in until J-CORE tags); otherwise the committed per-symbol AST digests of
the same file, which the first test proves equal to the git reference whenever one resolves."""

from __future__ import annotations

import ast
import hashlib
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
RUNNER = "packages/trestle-packs/trestle_packs/docker/runner.py"
REFS = ("wr-ckpt/core", "wr/core-bundle/core")
CHANGED = {"StackRunner.down", "StackRunner._teardown"}

# Per-symbol digests of runner.py at the core checkpoint (every symbol but the two BFD-49a ones).
BASELINE_DIGESTS = {
    "<module>": "f09c5b1289429d43",
    "StackResult": "c9406f4b8e56007f",
    "StackRunner.<class>": "e3fdad97ac4029dc",
    "StackRunner.__init__": "8d2b9ded5016a5c4",
    "StackRunner.up": "cccf06d6572e0283",
    "StackRunner._attach_service_logs": "51672928cc5011a2",
    "_wave_for_services": "0f26791b2915f5a9",
}


def symbol_digests(source: str) -> dict[str, str]:
    """Digest (ast.dump, so formatting and comments do not count) of every symbol."""
    tree = ast.parse(source)
    out: dict[str, str] = {}
    module_rest = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "StackRunner":
            header = [n for n in node.body if not isinstance(n, ast.FunctionDef)]
            out["StackRunner.<class>"] = _digest(
                ast.dump(ast.Module(body=[*header], type_ignores=[]))
                + ast.dump(ast.Tuple(elts=[*node.bases, *node.decorator_list]))
            )
            for member in node.body:
                if isinstance(member, ast.FunctionDef):
                    out[f"StackRunner.{member.name}"] = _digest(ast.dump(member))
        elif isinstance(node, (ast.ClassDef, ast.FunctionDef)):
            out[node.name] = _digest(ast.dump(node))
        else:
            module_rest.append(node)
    out["<module>"] = _digest(ast.dump(ast.Module(body=module_rest, type_ignores=[])))
    return out


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _git_reference() -> str | None:
    for ref in REFS:
        proc = subprocess.run(
            ["git", "show", f"{ref}:{RUNNER}"], cwd=ROOT, capture_output=True, text=True
        )
        if proc.returncode == 0:
            return proc.stdout
    return None


def _changed_symbols(old: dict[str, str], new: dict[str, str]) -> set[str]:
    return {name for name in old.keys() | new.keys() if old.get(name) != new.get(name)}


def test_baseline_digests_equal_the_git_reference() -> None:
    reference = _git_reference()
    if reference is None:
        pytest.skip("no wr-ckpt/core (or core bundle) ref reachable in this checkout")
    ref_digests = symbol_digests(reference)
    for name, digest in BASELINE_DIGESTS.items():
        assert ref_digests[name] == digest, name


def test_runner_changes_limited_to_teardown() -> None:
    current = symbol_digests((ROOT / RUNNER).read_text(encoding="utf-8"))
    reference = _git_reference()
    old = symbol_digests(reference) if reference is not None else dict(BASELINE_DIGESTS)
    assert set(current) - CHANGED == set(old) - CHANGED  # no symbol added or removed but the two
    changed = _changed_symbols(
        {k: v for k, v in old.items() if k not in CHANGED},
        {k: v for k, v in current.items() if k not in CHANGED},
    )
    assert changed == set(), sorted(changed)


def test_scope_guard_detects_a_planted_edit_outside_the_teardown_symbols() -> None:
    source = (ROOT / RUNNER).read_text(encoding="utf-8")
    planted = source.replace('msg = "stack spec has no services"', 'msg = "no services"')
    assert planted != source
    old = symbol_digests(source)
    assert _changed_symbols(old, symbol_digests(planted)) == {"StackRunner.up"}
