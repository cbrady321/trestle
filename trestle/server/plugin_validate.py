"""Plugin validation: throwaway subprocess, then snapshot (R-PLUG-16, R-PLUG-18)."""

from __future__ import annotations

import ast
import json
import subprocess
from pathlib import Path

from trestle.common.pyenv import build_child_env, python_argv

PACK_IMPORT_PREFIX = "trestle_packs"
VALIDATE_TIMEOUT_S = 10.0


class PluginValidationError(ValueError):
    """Throwaway-subprocess validation failed; do not snapshot."""


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
    """Probe trestle_packs in a throwaway interpreter (not the MCP process)."""
    try:
        proc = subprocess.run(
            python_argv("-c", "import trestle_packs"),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=VALIDATE_TIMEOUT_S,
            env=build_child_env(),
            start_new_session=True,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return "import trestle_packs timed out"
    if proc.returncode == 0:
        return None
    err = (proc.stderr or proc.stdout or "import failed").strip()
    return err[:200] if err else "import failed"


def validate_plugin(
    source_path: Path, *, entry: str | None = None, packages: tuple[str, ...] = ()
) -> str | None:
    """Import the plugin in a throwaway child. None means ok; str is diagnosis.

    `entry`, when given, is the entry name the publisher derived from the source; the child
    refuses a plugin whose one marked callable is not that. Each of `packages` must resolve on
    the child's import path."""
    error, _digests = validate_and_digest(source_path, entry=entry, packages=packages)
    return error


def validate_and_digest(
    source_path: Path, *, entry: str | None = None, packages: tuple[str, ...] = ()
) -> tuple[str | None, dict[str, str]]:
    """`validate_plugin`, and the digest the same throwaway child computed for each declared
    package (found on the import path the run's child will use, before any plugin code
    imports). The digests are empty when the plugin is refused."""
    argv = python_argv("-m", "trestle.child.validate", "--plugin", str(source_path))
    if entry is not None:
        argv += ["--entry", entry]
    for name in packages:
        argv += ["--package", name]
    try:
        proc = subprocess.run(
            argv,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=VALIDATE_TIMEOUT_S,
            env=build_child_env(),
            start_new_session=True,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return "plugin import timed out in throwaway validator", {}
    payload = _parse_child_payload(proc.stdout)
    if proc.returncode == 0 and payload.get("ok") is True:
        found = payload.get("packages")
        digests = {str(k): str(v) for k, v in found.items()} if isinstance(found, dict) else {}
        return None, digests
    if isinstance(payload.get("error"), str) and payload["error"]:
        return str(payload["error"])[:200], {}
    err = (proc.stderr or proc.stdout or "validation failed").strip()
    return (err[:200] if err else "validation failed"), {}


def _parse_child_payload(stdout: str) -> dict[str, object]:
    text = stdout.strip()
    if not text:
        return {}
    try:
        loaded = json.loads(text.splitlines()[-1])
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def validate_plugin_imports(source_path: Path) -> str | None:
    """Catalog/admit probe: packs missing after a successful snapshot."""
    if not plugin_imports_packs(source_path):
        return None
    return packs_import_error()
