"""Self-tests for plugin loading across the root and packs sessions
(L.P0-0a.2, CSC-12)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_plugin_loaded_in_root_and_packs_sessions() -> None:
    root_proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--markers"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert "@pytest.mark.proves" in root_proc.stdout

    packs_proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-c",
            "pyproject.toml",
            "--rootdir",
            ".",
            "--markers",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert "@pytest.mark.proves" in packs_proc.stdout


def test_bare_packs_invocation_documented_as_unguarded() -> None:
    """CSC-12: a bare `pytest packages/trestle-packs/tests` skips the root
    conftest (its own pyproject.toml becomes rootdir), so the plugin never
    loads there. This is documented at the call site, not silently relied
    on."""
    conftest_text = (ROOT / "conftest.py").read_text()
    assert "packs-only session" in conftest_text
    assert "-c pyproject.toml --rootdir ." in conftest_text
