"""G-E3: (a) the legacy pytest runner never counts setup errors (BFD-46);
(b) `integration_pipeline` stops its stack exactly once on every path (K-15),
flipped by L.NW-1.2 (it used to tear down twice when `up` fails and never on
success). Both run in a subprocess driver against a lane-local fake ComposeBackend
(never an engine)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.proof import tolerances
from tests.proof.markers import target_check

ROOT = Path(__file__).resolve().parents[3]
DRIVER = Path(__file__).resolve().parent / "driver_g_e3.py"


def _drive(mode: str) -> dict[str, Any]:
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join([str(ROOT), str(ROOT / "packages" / "trestle-packs")]),
    }
    env.pop("TRESTLE_PROOF_GATE", None)
    proc = subprocess.run(
        [sys.executable, str(DRIVER), mode],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=tolerances.JOIN_WAIT_S,
    )
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT ")]
    assert lines, proc.stdout + proc.stderr
    return json.loads(lines[-1][len("RESULT ") :])


@pytest.mark.pin("G-E3")
def test_pin_setup_error_counts_zero() -> None:
    result = _drive("run_pytest_setup_error")
    assert result["collected"] == 1
    assert result["exit_code"] != 0
    assert result["errors"] == 0
    assert result["failed"] == 0
    assert result["passed"] == 0


@pytest.mark.target("G-E3")
@pytest.mark.proves(
    "WR-VERIFY-4", "WR-VERIFY-4:legacy-run_pytest-errors", "core", "core", "must", "CI"
)
@pytest.mark.xfail(strict=True, reason="defect:G-E3")
def test_target_setup_error_counted_not_passed() -> None:
    result = _drive("run_pytest_setup_error")
    target_check(
        result["errors"] == 1 and result["passed"] == 0,
        "G-E3",
        f"setup error not counted: errors={result['errors']} passed={result['passed']}",
    )


@pytest.mark.proves(
    "WR-ENV-11", "WR-ENV-11:pipeline-one-teardown@fake", "core", "core", "must", "CI"
)
def test_target_pipeline_down_exactly_once() -> None:
    failed = _drive("pipeline_fail")
    assert failed["raised"]
    ok = _drive("pipeline_ok")
    assert ok["raised"] == ""
    target_check(
        failed["down"] == 1 and ok["down"] == 1,
        "G-E3",
        f"teardown count: failure={failed['down']} success={ok['down']}, expected 1 and 1",
    )
