"""The build a `trestle` process runs: the package version, and for an editable checkout the
commit it was started from, so `trestle service status` can tell a stale service from a current
one even when the version string has not moved."""

from __future__ import annotations

import subprocess
from pathlib import Path

import trestle

PACKAGE_DIR = Path(trestle.__file__).resolve().parent


def checkout_dir() -> Path | None:
    """The git checkout this package is imported from (an editable install), else None."""
    root = PACKAGE_DIR.parent
    return root if (root / ".git").exists() and (root / "pyproject.toml").is_file() else None


def build_version() -> str:
    """`<version>` for an installed wheel; `<version>+g<sha>` (with `.dirty` when the tree has
    uncommitted changes) for an editable checkout."""
    version = trestle.__version__
    root = checkout_dir()
    if root is None:
        return version
    try:
        sha = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short=12", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return version
    return f"{version}+g{sha}{'.dirty' if dirty else ''}"
