"""Self-tests for plugin loading across the root and packs sessions
(L.P0-0a.2, CSC-12), and the import-origin guard (L.P0-0b.5)."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
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


def test_import_origin_guard_rejects_stale_packs_copy() -> None:
    """A `trestle_packs` copy that resolves outside the rootdir and
    diverges from `packages/trestle-packs/trestle_packs` must fail the
    guard — the same shape as the stale miniforge site-packages copy this
    host carries when `PYTHONPATH` is not set."""
    with tempfile.TemporaryDirectory() as tmp:
        stale_root = Path(tmp) / "stale"
        stale_pkg = stale_root / "trestle_packs"
        stale_pkg.mkdir(parents=True)
        (stale_pkg / "__init__.py").write_text("PLANTED_DIVERGENT = True\n", encoding="utf-8")

        script = (
            "import sys; "
            f"sys.path.insert(0, {str(stale_root)!r}); "
            f"sys.path.insert(1, {str(ROOT)!r}); "
            "from tests.proof import guards; guards.check_import_origin()"
        )
        proc = subprocess.run(
            [sys.executable, "-c", script],
            env={k: v for k, v in os.environ.items() if k != "PYTHONPATH"},
            capture_output=True,
            text=True,
        )
        assert proc.returncode == 3, proc.stdout + proc.stderr
        assert "trestle_packs" in proc.stderr


def test_import_origin_guard_accepts_canonical_packs_copy() -> None:
    """The counterpart of the rejection test: with the real
    `packages/trestle-packs` on `PYTHONPATH`, the guard passes."""
    proc = subprocess.run(
        [sys.executable, "-c", "from tests.proof import guards; guards.check_import_origin()"],
        cwd=ROOT,
        env={
            **os.environ,
            "PYTHONPATH": str(ROOT / "packages" / "trestle-packs"),
        },
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
