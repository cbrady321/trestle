"""Lightweight plugin import validation for optional pack dependencies."""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

PACK_IMPORT_PREFIX = "trestle_packs"


def _imports_packs_module(name: str) -> bool:
    return name == PACK_IMPORT_PREFIX or name.startswith(f"{PACK_IMPORT_PREFIX}.")


def plugin_imports_packs(source_path: Path) -> bool:
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _imports_packs_module(alias.name):
                    return True
        if isinstance(node, ast.ImportFrom) and node.module:
            if _imports_packs_module(node.module):
                return True
    return False


def packs_import_error() -> str | None:
    try:
        importlib.import_module(PACK_IMPORT_PREFIX)
    except ImportError as exc:
        return str(exc)
    return None


def validate_plugin_imports(source_path: Path) -> str | None:
    if not plugin_imports_packs(source_path):
        return None
    return packs_import_error()
