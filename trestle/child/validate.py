"""Throwaway plugin validation (R-PLUG-16). Must not import Kernel or FastMCP."""

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import cast

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
    args = parser.parse_args(argv)
    path = Path(args.plugin)
    try:
        _load_plugin(path, args.entry)
    except ValidationFailed as exc:
        print(json.dumps({"ok": False, "error": str(exc)[:500]}), flush=True)
        return 1
    except Exception as exc:
        print(
            json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"[:500]}),
            flush=True,
        )
        return 1
    print(json.dumps({"ok": True}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
