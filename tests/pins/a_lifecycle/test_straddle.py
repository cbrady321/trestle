"""F-E4-1 straddle (BFD-05 / root B.6): an S0-started, non-terminal run with
no recorded process identity, recovered by S0's `recover_run_dir`.

Under K-19 (Q-STRADDLE-ORPHAN answered (a)) the whole obligation for such a
pre-identity run is B2-C11 branch (i): no signal, `confirmed_gone` false,
cleanup `unknown`, `interrupted` -- the stop is unconfirmed, never clean. S0
signals nothing and does no liveness check (the pin), but it reports neither
`confirmed_gone` nor a cleanup disposition (the two G-A3 targets).

Nothing here argv-scans, sweeps or signals the S0-started process (CSC-14).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.pins.a_lifecycle import straddle
from tests.proof import records
from tests.proof.markers import target_check
from trestle.server.recovery import recover_run_dir

LABEL = "WR-CANCEL-3:s0-straddle-stop-unconfirmed-never-clean"


def _stop_report(run_dir: Path) -> dict[str, Any]:
    """What the run's record says about the stop: any `confirmed_gone` field
    on a ledger row and any `cleanup` field on a ledger row or in meta.json
    (B2-C11 / `GroupStop`, `CleanupAnswer`). S0 records neither."""
    report: dict[str, Any] = {}
    rows = records.ledger_rows(run_dir).rows
    for row in rows:
        for key in ("confirmed_gone", "cleanup"):
            if key in row:
                report[key] = row[key]
    meta_path = run_dir / "evidence" / "meta.json"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if "cleanup" in meta:
            report["cleanup"] = meta["cleanup"]
        if "confirmed_gone" in meta:
            report["confirmed_gone"] = meta["confirmed_gone"]
    return report


def _assert_unconfirmed_never_clean(run_dir: Path) -> None:
    report = _stop_report(run_dir)
    target_check(
        report.get("confirmed_gone") is False and report.get("cleanup") == "unknown",
        "G-A3",
        f"recovery reports the stop as {report!r}, not confirmed_gone=False with cleanup 'unknown'",
    )


@pytest.mark.pin("G-A3")
def test_pin_s0_recovery_interrupts_without_liveness_check(tmp_path: Path) -> None:
    run_dir = straddle.straddle_run_dir(tmp_path)
    assert records.node_record(run_dir).terminal is None
    with straddle.spawn_s0_shaped_orphan(run_dir) as orphan:
        recover_run_dir(run_dir)
        # S0 reads ledger kinds only: the live orphan changes nothing.
        assert records.node_record(run_dir).terminal == "interrupted"
        assert straddle.is_alive(orphan), "S0 recovery signalled the orphan"


@pytest.mark.target("G-A3")
@pytest.mark.proves("WR-CANCEL-3", LABEL, "core", "core", "PROC", "BOTH")
@pytest.mark.xfail(strict=True, reason="defect:G-A3")
def test_target_s0_straddle_branch_i_unconfirmed_never_clean(tmp_path: Path) -> None:
    run_dir = straddle.straddle_run_dir(tmp_path)
    recover_run_dir(run_dir)
    assert records.node_record(run_dir).terminal == "interrupted"
    _assert_unconfirmed_never_clean(run_dir)


@pytest.mark.target("G-A3")
@pytest.mark.proves("WR-CANCEL-3", LABEL, "core", "core", "PROC", "BOTH")
@pytest.mark.xfail(strict=True, reason="defect:G-A3")
def test_target_s0_orphan_not_signalled_branch_i_unconfirmed_never_clean(tmp_path: Path) -> None:
    run_dir = straddle.straddle_run_dir(tmp_path)
    with straddle.spawn_s0_shaped_orphan(run_dir) as orphan:
        recover_run_dir(run_dir)
        assert straddle.is_alive(orphan), "recovery signalled the S0-started orphan"
        assert records.node_record(run_dir).terminal == "interrupted"
        _assert_unconfirmed_never_clean(run_dir)
