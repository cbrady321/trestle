"""`python -m tests.proof.meta drain-check [--home DIR]` (CSC-5 builder L.SV-3.8; MC-31 row SV-3).

SV-3 is the first plan-bearing version: a reader older than it neither folds nor sweeps a run whose
`spec.json` carries a `plan`. The rollback posture of SV-3 is therefore `drain`: before rolling
back past it, no plan-bearing root may still be live. This counts the roots under a Trestle home
whose spec carries a plan and whose ledger is not finalized terminal, and exits 1 while there is
any.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from trestle.server.ledger import RunLedger, evidence_dir, ledger_path


def live_plan_bearing_roots(home: Path) -> list[Path]:
    """Run directories of `home` with a `plan` in their spec and no finalized terminal state."""
    runs_root = home / "runs"
    live: list[Path] = []
    if not runs_root.is_dir():
        return live
    for month in sorted(runs_root.iterdir()):
        if not month.is_dir():
            continue
        for run_dir in sorted(month.iterdir()):
            spec_path = evidence_dir(run_dir) / "spec.json"
            if not spec_path.is_file() or not ledger_path(run_dir).is_file():
                continue
            try:
                spec = json.loads(spec_path.read_text(encoding="utf-8"))
            except ValueError:
                continue
            if not isinstance(spec, dict) or "plan" not in spec:
                continue
            ledger = RunLedger.open(ledger_path(run_dir))
            finalized = ledger.terminal_state() is not None and ledger.has_kind(
                "evidence_finalized"
            )
            if not finalized:
                live.append(run_dir)
    return live


def cmd_drain_check(args: argparse.Namespace) -> int:
    home = (
        Path(args.home)
        if args.home
        else Path(os.environ.get("TRESTLE_HOME", Path.home() / ".trestle"))
    )
    live = live_plan_bearing_roots(home)
    print(f"drain-check: {len(live)} non-terminal plan-bearing root(s) under {home}")
    for run_dir in live:
        print(f"drain-check: live {run_dir.name}")
    return 1 if live else 0
