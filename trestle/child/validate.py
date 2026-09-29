"""Throwaway plugin validation (R-PLUG-16). Must not import Kernel or FastMCP."""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.machinery
import importlib.util
import json
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import cast

from trestle.common.fsutil import sha256_file
from trestle.plugin.surface import is_trestle_plugin

FORBIDDEN_PREFIXES = (
    "trestle.server",
    "trestle.wrapper",
    "trestle.child",
    "trestle.query",
    "trestle.ops",
    "trestle.cli",
    "fastmcp",
    "mcp",
)
ALLOWED_TRESTLE_PREFIXES = ("trestle.plugin",)


def forbidden_import_error(source: str) -> str | None:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
        for name in names:
            if _is_forbidden(name):
                return (
                    f"plugin imports {name!r}; only PluginSurface "
                    f"(trestle.plugin) is permitted (R-PLUG-6)"
                )
    return None


def _is_forbidden(name: str) -> bool:
    if name == "trestle" or name.startswith("trestle."):
        return not any(
            name == prefix or name.startswith(f"{prefix}.") for prefix in ALLOWED_TRESTLE_PREFIXES
        )
    return any(name == prefix or name.startswith(f"{prefix}.") for prefix in FORBIDDEN_PREFIXES)


def select_entry(module: ModuleType, entry: str | None = None) -> Callable[..., object]:
    """The one entry callable of a loaded plugin module (WR-PLAN-4).

    A callable counts only when it is marked and defined in the module itself: a marked
    callable the plugin imports is never the entry. With `entry`, that name must be the one
    marked definition; without it (a snapshot that predates the manifest's `entry`) there
    must be exactly one. Anything else raises `ValidationFailed`."""
    marked = {
        name: obj
        for name, obj in vars(module).items()
        if callable(obj)
        and is_trestle_plugin(obj)
        and getattr(obj, "__module__", None) == module.__name__
    }
    if entry is not None:
        found = marked.get(entry)
        if found is None:
            raise ValidationFailed(f"entry point {entry!r} is not a @trestle function defined here")
    elif not marked:
        raise ValidationFailed("no @trestle plugin defined in the plugin file")
    else:
        found = next(iter(marked.values()))
    if len(marked) > 1:
        raise ValidationFailed(f"ambiguous entry point: {', '.join(sorted(marked))}")
    return cast(Callable[..., object], found)


class ProvenanceMismatch(Exception):
    """A declared package is missing or no longer digests to what publication recorded."""


def _locate_package(name: str) -> Path | None:
    """The file or directory a declared package name resolves to on this interpreter's import
    path, found without importing anything: a regular package or module first, a namespace
    directory only when nothing regular is found. The working directory is never searched
    (`python -P`, WR-PLAN-6)."""
    parts = name.split(".")
    namespace: Path | None = None
    for entry in sys.path:
        if not entry:
            continue
        base = Path(entry)
        if not base.is_dir():
            continue
        found = _resolve_in(base, parts)
        if found is None:
            continue
        if found.is_dir() and not (found / "__init__.py").is_file():
            namespace = namespace or found
            continue
        return found
    return namespace


def _resolve_in(base: Path, parts: list[str]) -> Path | None:
    cur = base
    for index, part in enumerate(parts):
        last = index == len(parts) - 1
        pkg = cur / part
        if pkg.is_dir() and (pkg / "__init__.py").is_file():
            if last:
                return pkg
            cur = pkg
            continue
        for suffix in (".py", *importlib.machinery.EXTENSION_SUFFIXES):
            module = cur / f"{part}{suffix}"
            if module.is_file():
                return module if last else None
        if pkg.is_dir():  # a namespace directory
            if last:
                return pkg
            cur = pkg
            continue
        return None
    return None


def package_digest(name: str) -> str:
    """The digest of one declared package: the file digest of a module, or, for a package, a
    digest over the relative path and file digest of every `.py` file under it, in path order.
    Raises `ProvenanceMismatch` when the name does not resolve."""
    if name in sys.builtin_module_names:
        return hashlib.sha256(f"builtin:{name}".encode()).hexdigest()
    located = _locate_package(name)
    if located is None:
        raise ProvenanceMismatch(f"declared package {name!r} is not found on the import path")
    if located.is_file():
        return sha256_file(located)
    digest = hashlib.sha256()
    for path in sorted(p for p in located.rglob("*.py") if "__pycache__" not in p.parts):
        digest.update(path.relative_to(located).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(sha256_file(path).encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()


def package_digests(names: list[str] | tuple[str, ...]) -> dict[str, str]:
    return {name: package_digest(name) for name in names}


def check_package_digests(expected: dict[str, str]) -> None:
    """Compare each recorded package digest with what this interpreter resolves now. Raises
    `ProvenanceMismatch` naming the first package that is missing or changed; imports nothing,
    so it runs before any plugin code."""
    for name in sorted(expected):
        try:
            found = package_digest(name)
        except ProvenanceMismatch as exc:
            raise ProvenanceMismatch(f"{exc} (recorded at publication)") from exc
        if found != expected[name]:
            raise ProvenanceMismatch(
                f"declared package {name!r} changed since publication "
                f"(recorded {expected[name][:12]}, found {found[:12]})"
            )


def _load_plugin(path: Path, entry: str | None = None) -> None:
    source = path.read_text(encoding="utf-8")
    forbidden = forbidden_import_error(source)
    if forbidden is not None:
        raise ValidationFailed(forbidden)
    spec = importlib.util.spec_from_file_location("trestle_plugin_validate", path)
    if spec is None or spec.loader is None:
        raise ValidationFailed(f"cannot load plugin: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["trestle_plugin_validate"] = module
    spec.loader.exec_module(module)
    select_entry(module, entry)


class ValidationFailed(Exception):
    """Plugin failed throwaway-subprocess checks."""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="trestle-validate")
    parser.add_argument("--plugin", required=True)
    parser.add_argument("--entry")
    parser.add_argument("--package", action="append", default=[])
    args = parser.parse_args(argv)
    path = Path(args.plugin)
    try:
        # the declared packages are digested first, before any plugin code is imported
        digests = package_digests(args.package)
        _load_plugin(path, args.entry)
    except (ValidationFailed, ProvenanceMismatch) as exc:
        print(json.dumps({"ok": False, "error": str(exc)[:500]}), flush=True)
        return 1
    except Exception as exc:
        print(
            json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"[:500]}),
            flush=True,
        )
        return 1
    print(json.dumps({"ok": True, "packages": digests}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
