"""Self-tests for `python -m tests.proof.meta mypy-ratchet` (L.P0-0a.6)."""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
BASELINE_PATH = ROOT / "tests" / "proof" / "baseline.json"


def test_s0_single_error_passes() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "tests.proof.meta", "mypy-ratchet", "--max", "1"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def _run_with_planted_sites(sites: list[str], max_errors: int) -> subprocess.CompletedProcess[str]:
    """Run mypy-ratchet against a monkeypatched `mypy_error_sites()` and a
    planted baseline, so this test never depends on actually injecting a
    real second type error into `trestle/`."""
    script = textwrap.dedent(
        f"""
        import sys, json
        sys.path.insert(0, {str(ROOT)!r})
        import tests.proof.meta as m
        m.mypy_error_sites = lambda: {sites!r}
        sys.exit(m.main(["mypy-ratchet", "--max", "{max_errors}"]))
        """
    )
    return subprocess.run([sys.executable, "-c", script], cwd=ROOT, capture_output=True, text=True)


def test_planted_second_error_fails() -> None:
    baseline_sites = json.loads(BASELINE_PATH.read_text())["ev01"]["mypy_error_sites"]
    proc = _run_with_planted_sites(
        baseline_sites + ["trestle/server/conductor.py:99"], max_errors=1
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr


def test_error_moved_location_fails() -> None:
    # Same count (1) as the recorded baseline, but at a site the baseline
    # never named: a ratchet on count alone would let this through.
    proc = _run_with_planted_sites(["trestle/server/conductor.py:99"], max_errors=1)
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "not in the recorded baseline" in proc.stdout
