"""Throwaway plugin validation (R-PLUG-16). Must not import Kernel or FastMCP."""

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import sys
from pathlib import Path

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


def _load_plugin(path: Path) -> None:
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
    for name in dir(module):
        obj = getattr(module, name)
        if callable(obj) and is_trestle_plugin(obj):
            return
    raise ValidationFailed(f"no @trestle plugin in {path}")


class ValidationFailed(Exception):
    """Plugin failed throwaway-subprocess checks."""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="trestle-validate")
    parser.add_argument("--plugin", required=True)
    args = parser.parse_args(argv)
    path = Path(args.plugin)
    try:
        _load_plugin(path)
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
