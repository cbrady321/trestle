"""Self-tests for `python -m tests.proof.meta baseline` (L.P0-0a.1)."""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
BASELINE_PATH = ROOT / "tests" / "proof" / "baseline.json"
NODEIDS_PATH = ROOT / "tests" / "fixtures" / "golden" / "s0" / "nodeids.txt"


@pytest.mark.proves("WR-PROOF-8", "WR-PROOF-8:baseline-preserved", "core", "core", "must", "CI")
def test_baseline_312_matches_ev01_or_explained() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "tests.proof.meta", "baseline"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.proves("WR-COMPAT-12", "WR-COMPAT-12:preserved", "core", "core", "must", "CI")
def test_nodeids_are_187_unique() -> None:
    ids = [line for line in NODEIDS_PATH.read_text().splitlines() if line.strip()]
    assert len(ids) == 187
    assert len(set(ids)) == 187


def test_meta_baseline_exits_1_on_unexplained_field(tmp_path: Path) -> None:
    data = json.loads(BASELINE_PATH.read_text())
    # Plant an unexplained mismatch: EV-01 says 173, nothing will ever measure
    # 999999, and no explanation entry names root_passed.
    data["ev01"]["root_passed"] = 999999
    planted = tmp_path / "planted_baseline.json"
    planted.write_text(json.dumps(data))

    script = textwrap.dedent(
        f"""
        import sys
        sys.path.insert(0, {str(ROOT)!r})
        from pathlib import Path
        import tests.proof.meta as m
        m.BASELINE_PATH = Path({str(planted)!r})
        sys.exit(m.main(["baseline"]))
        """
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 1, proc.stdout + proc.stderr
    assert "root_passed" in proc.stdout
