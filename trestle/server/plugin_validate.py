"""Plugin validation: throwaway subprocess, then snapshot (R-PLUG-16, R-PLUG-18)."""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

PACK_IMPORT_PREFIX = "trestle_packs"
VALIDATE_TIMEOUT_S = 10.0


class PluginValidationError(ValueError):
    """Throwaway-subprocess validation failed; do not snapshot."""


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    root = _repo_root()
    parts = [str(root)]
    packs = root / "packages" / "trestle-packs"
    if packs.is_dir():
        parts.append(str(packs))
    existing = env.get("PYTHONPATH", "")
    prefix = os.pathsep.join(parts)
    env["PYTHONPATH"] = prefix if not existing else f"{prefix}{os.pathsep}{existing}"
    return env


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
            [sys.executable, "-c", "import trestle_packs"],
            capture_output=True,
            text=True,
            timeout=VALIDATE_TIMEOUT_S,
            env=_subprocess_env(),
            start_new_session=True,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return "import trestle_packs timed out"
    if proc.returncode == 0:
        return None
    err = (proc.stderr or proc.stdout or "import failed").strip()
    return err[:200] if err else "import failed"


def validate_plugin(source_path: Path) -> str | None:
    """Import the plugin in a throwaway child. None means ok; str is diagnosis."""
    try:
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "trestle.child.validate",
                "--plugin",
                str(source_path),
            ],
            capture_output=True,
            text=True,
            timeout=VALIDATE_TIMEOUT_S,
            env=_subprocess_env(),
            start_new_session=True,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return "plugin import timed out in throwaway validator"
    payload = _parse_child_payload(proc.stdout)
    if proc.returncode == 0 and payload.get("ok") is True:
        return None
    if isinstance(payload.get("error"), str) and payload["error"]:
        return str(payload["error"])[:200]
    err = (proc.stderr or proc.stdout or "validation failed").strip()
    return err[:200] if err else "validation failed"


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
