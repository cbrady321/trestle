"""R6a: a plugin source's undeclared non-stdlib imports, read statically (no import).

In `trestle.common` so the host (the registry) and the plugin side can both read it: the host may
not import `trestle.child.validate` (DM-17)."""

from __future__ import annotations

import ast
import sys
from collections.abc import Sequence


def undeclared_imports(source: str, packages: Sequence[str]) -> list[str]:
    """R6a: the top-level names of the non-stdlib modules `source` imports that no entry of
    `packages` covers, sorted. Such code is not recorded or checked at run start (R-J), so
    publication warns about it; it never refuses. Trestle itself, `__future__`, the standard
    library and relative imports are left out; `packages=["a"]` covers `a` and every `a.*`."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names = [node.module]
        else:
            continue
        for name in names:
            top = name.split(".", 1)[0]
            if top in ("trestle", "__future__") or top in sys.stdlib_module_names:
                continue
            if any(name == p or name.startswith(f"{p}.") for p in packages):
                continue
            found.add(top)
    return sorted(found)


def undeclared_import_warning(source: str, packages: Sequence[str]) -> str | None:
    """The publication warning for `undeclared_imports`, or None when there is nothing to say."""
    missing = undeclared_imports(source, packages)
    if not missing:
        return None
    names = ", ".join(repr(m) for m in missing)
    return (
        f"imports {names} without declaring it in @trestle(packages=[...]): that code is not "
        "recorded at publication or checked at run start, so an edit to it runs unnoticed"
    )
