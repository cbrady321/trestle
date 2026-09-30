"""L.SV-3.8: `python -m tests.proof.meta drain-check` counts the non-terminal plan-bearing roots
under a Trestle home (the SV-3 drain boundary, MC-31): exit 1 while any is live."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from tests.proof.drain_check import live_plan_bearing_roots
from trestle.common.fsutil import atomic_write_json
from trestle.server.ledger import RunLedger, evidence_dir, ledger_path
from trestle.server.recovery import recover_run_dir, seed_interrupted_run

REPO = Path(__file__).resolve().parents[3]


def _seed(home: Path, run_id: str, *, plan: bool, last_kind: str = "started") -> Path:
    run_dir = seed_interrupted_run(home, run_id, last_kind=last_kind)
    spec: dict[str, object] = {"plugin": "echo"}
    if plan:
        spec["plan"] = {"format_version": 1}
    atomic_write_json(evidence_dir(run_dir) / "spec.json", spec)
    return run_dir


def _drain_check(home: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "tests.proof.meta", "drain-check", "--home", str(home)],
        cwd=REPO,
        env={"PYTHONPATH": str(REPO), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=False,
    )


def test_drain_check_counts_nonterminal_plan_bearing_roots(tmp_path: Path) -> None:
    home = tmp_path / "home"
    # an empty home drains trivially
    assert _drain_check(home).returncode == 0

    live = _seed(home, "r_live_plan", plan=True)
    planless = _seed(home, "r_live_planless", plan=False)  # not plan-bearing: not counted
    finished = _seed(home, "r_done_plan", plan=True)
    recover_run_dir(finished)  # finalized terminal (interrupted)
    assert RunLedger.open(ledger_path(finished)).terminal_state() == "interrupted"

    assert live_plan_bearing_roots(home) == [live]
    blocked = _drain_check(home)
    assert blocked.returncode == 1, blocked.stdout
    assert "1 non-terminal plan-bearing root" in blocked.stdout and "r_live_plan" in blocked.stdout
    assert planless.name not in blocked.stdout

    recover_run_dir(live)  # recovery finalizes it; the planless root does not matter
    drained = _drain_check(home)
    assert drained.returncode == 0, drained.stdout
    assert "0 non-terminal plan-bearing root" in drained.stdout
    assert json.loads((evidence_dir(planless) / "spec.json").read_text())["plugin"] == "echo"
