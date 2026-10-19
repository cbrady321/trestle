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

import trestle.plugin as _plugin_package
import trestle.workflow as _workflow_package
from trestle.common import codes
from trestle.common.fsutil import sha256_file
from trestle.common.plan import declared as _declared
from trestle.common.plan.declared import DeclaredTree
from trestle.common.plan.vocabulary import SINGLE_LEVEL_CODES, TREE_PUBLICATION_CODES
from trestle.plugin.surface import is_trestle_plugin
from trestle.workflow.declarations import WorkflowEntry
from trestle.workflow.extract import (
    ExtractionRefused,
    extract_declared_tree,
    publication_refusal,
)

# The registration refusals (B1-E1; L.SL-7.1) the extractor may report: each is its own stable
# `publication.*` code from the single-level vocabulary, never folded into declaration_invalid.
PUBLICATION_REFUSAL_CODES = frozenset(
    c for c in SINGLE_LEVEL_CODES if c.startswith("publication.")
) | frozenset(TREE_PUBLICATION_CODES)

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
# Exact module names, not prefixes: the codec (trestle.plugin._codec) lives under
# trestle.plugin and must stay unimportable by plugins (MC-CORE-08).
# The public names of `trestle.workflow` (the declaration data types; L.SV-2.3) are admitted the
# same way: the package, and the submodules it lists as public. Never `trestle.workflow._*`.
ALLOWED_TRESTLE_PREFIXES = (
    "trestle.plugin",
    "trestle.plugin.surface",
    "trestle.workflow",
    *(f"trestle.workflow.{name}" for name in _workflow_package.PUBLIC_MODULES),
)
# `from trestle.plugin import X` may name only the package's exports and `surface`.
_PLUGIN_PACKAGE_NAMES = frozenset({*_plugin_package.__all__, "surface"})
# `from trestle.workflow import X` may name only its exports and its public submodules.
_WORKFLOW_PACKAGE_NAMES = frozenset({*_workflow_package.__all__, *_workflow_package.PUBLIC_MODULES})


def forbidden_import_error(source: str) -> str | None:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
            if node.module == "trestle.plugin":
                names.extend(
                    f"trestle.plugin.{alias.name}"
                    for alias in node.names
                    if alias.name not in _PLUGIN_PACKAGE_NAMES
                )
            elif node.module == "trestle.workflow":
                names.extend(
                    f"trestle.workflow.{alias.name}"
                    for alias in node.names
                    if alias.name not in _WORKFLOW_PACKAGE_NAMES
                )
        for name in names:
            if _is_forbidden(name):
                return (
                    f"plugin imports {name!r}; only PluginSurface "
                    f"(trestle.plugin) is permitted (R-PLUG-6)"
                )
    return None


def _is_forbidden(name: str) -> bool:
    if name == "trestle" or name.startswith("trestle."):
        return name not in ALLOWED_TRESTLE_PREFIXES
    return any(name == prefix or name.startswith(f"{prefix}.") for prefix in FORBIDDEN_PREFIXES)


# Port modules a workflow plugin reaches the machine through (D-b, B1-E1): the workflow package's
# own `ports` module and the packs' port packages. A plugin importing any of them acts on an
# environment, so it must declare which argument names it (`@trestle(env_arg=...)`, WR-OWN-8).
PORT_MODULES: tuple[str, ...] = (
    "process",
    "fakes",
    "container",
    "toolchain",
    "grant",
    "provision",
    "testrun",
)
_PORT_PREFIXES: tuple[str, ...] = (
    "trestle.workflow.ports",
    *(f"trestle_packs.{name}" for name in PORT_MODULES),
)


def imported_port_modules(source: str) -> list[str]:
    """The port modules `source` imports (sorted, unique), read from the AST alone: the plugin
    is not imported. `from trestle_packs import process` counts as importing the port."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.append(node.module)
            names.extend(f"{node.module}.{alias.name}" for alias in node.names)
        for name in names:
            if any(name == p or name.startswith(f"{p}.") for p in _PORT_PREFIXES):
                found.add(name)
    return sorted(found)


def env_declaration_error(
    ports: list[str], env_arg: str | None, tree: DeclaredTree | None
) -> tuple[str, str] | None:
    """The D-b publication rule (B1-E1, WR-OWN-8): `(wire code, message)` or None.

    * A plugin importing a port module must declare `env_arg`.
    * A plugin with a declared tree must agree with itself: the root declaration's
      `env_key_field` equals the plugin's `env_arg` (both absent is fine).
    """
    if ports and env_arg is None:
        return (
            codes.PUBLICATION_ENV_ARG_MISSING,
            f"plugin imports port module {ports[0]!r} but declares no env_arg; "
            "declare @trestle(env_arg=...) naming the environment argument (WR-OWN-8)",
        )
    if tree is None:
        return None
    key = tree.nodes[""].get("env_key_field")
    if key == env_arg:
        return None
    if key is None:
        return (
            codes.PUBLICATION_ENV_ARG_MISSING,
            f"the root declaration names no env_key_field but the plugin declares env_arg "
            f"{env_arg!r}; the two must be equal (WR-OWN-8)",
        )
    if env_arg is None:
        return (
            codes.PUBLICATION_ENV_ARG_MISSING,
            f"the root declaration names env_key_field {key!r} but the plugin declares no "
            "env_arg; declare @trestle(env_arg=...) (WR-OWN-8)",
        )
    return (
        codes.PUBLICATION_DECLARATION_INVALID,
        f"the root declaration's env_key_field {key!r} differs from env_arg {env_arg!r}",
    )


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


def _load_plugin(path: Path, entry: str | None = None) -> ModuleType:
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
    return module


def declared_tree_of(
    module: ModuleType, root_eligibility: _declared.RootEligibility | None = None
) -> DeclaredTree | None:
    """The declared tree of a workflow plugin (MC-34), or None for a plain plugin.

    A workflow plugin binds one module-level `WorkflowEntry` (B1-C8); the tree is extracted from
    it here, in the throwaway validator, because this is the only place plugin code is imported at
    publication. More than one entry, or an entry whose extraction fails, raises
    `ExtractionRefused` (never a partial declaration), and so does a tree the declaration alone
    shows to be unpublishable (`publication_refusal`, L.TR-0.4; `root_eligibility` is OQ-31's
    variant, default the shipped one)."""
    entries = {id(v): v for v in vars(module).values() if isinstance(v, WorkflowEntry)}
    if not entries:
        return None
    if len(entries) > 1:
        raise ExtractionRefused(
            module.__name__, f"{len(entries)} WorkflowEntry objects; expected one"
        )
    tree = extract_declared_tree(next(iter(entries.values())))
    refusal = publication_refusal(
        tree, root_eligibility if root_eligibility is not None else _declared.ROOT_ELIGIBILITY
    )
    if refusal is not None:
        raise refusal
    return tree


class ValidationFailed(Exception):
    """Plugin failed throwaway-subprocess checks."""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="trestle-validate")
    parser.add_argument("--plugin", required=True)
    parser.add_argument("--entry")
    parser.add_argument("--package", action="append", default=[])
    parser.add_argument("--env-arg")
    parser.add_argument("--root-eligibility", choices=["refuse_at_publication", "admit_and_stop"])
    args = parser.parse_args(argv)
    path = Path(args.plugin)
    try:
        # the declared packages are digested first, before any plugin code is imported
        digests = package_digests(args.package)
        tree: DeclaredTree | None = None
        ports = imported_port_modules(path.read_text(encoding="utf-8"))
        # the D-b rule's static half needs no import of code that may not even load
        refusal = env_declaration_error(ports, args.env_arg, None) if ports else None
        if refusal is None:
            module = _load_plugin(path, args.entry)
            tree = declared_tree_of(module, args.root_eligibility)
            refusal = env_declaration_error(ports, args.env_arg, tree)
        if refusal is not None:
            code, message = refusal
            print(json.dumps({"ok": False, "code": code, "error": message[:500]}), flush=True)
            return 1
    except (ValidationFailed, ProvenanceMismatch) as exc:
        print(json.dumps({"ok": False, "error": str(exc)[:500]}), flush=True)
        return 1
    except ExtractionRefused as exc:
        # extraction failed: publication is refused with its own stable code (no snapshot)
        payload = {
            "ok": False,
            "error": str(exc)[:500],
            "code": exc.code
            if exc.code in PUBLICATION_REFUSAL_CODES
            else codes.PUBLICATION_DECLARATION_INVALID,
        }
        print(json.dumps(payload), flush=True)
        return 1
    except Exception as exc:
        print(
            json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"[:500]}),
            flush=True,
        )
        return 1
    result: dict[str, object] = {"ok": True, "packages": digests}
    if tree is not None:
        result["declaration"] = tree.to_json()
    print(json.dumps(result), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
