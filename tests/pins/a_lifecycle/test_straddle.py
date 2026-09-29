"""F-E4-1 straddle (BFD-05 / root B.6), flipped by L.CS-2.4: an S0-started, non-terminal run
with no recorded process identity, recovered by today's `recover_run_dir`.

Under K-19 (Q-STRADDLE-ORPHAN answered (a)) the whole obligation for such a pre-identity run is
B2-C11 branch (i): no signal, `confirmed_gone` false, cleanup `unknown`, `interrupted` -- the stop
is unconfirmed, never clean. The S0 today-pin ("nothing is reported") is retired with this flip.

Nothing here argv-scans, sweeps or signals the S0-started process (CSC-14).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.pins.a_lifecycle import straddle
from tests.proof import harness, records
from tests.proof.markers import target_check
from trestle.server.recovery import recover_run_dir

LABEL = "WR-CANCEL-3:s0-straddle-stop-unconfirmed-never-clean"


def _stop_report(run_dir: Path) -> dict[str, Any]:
    """What the run's record says about the stop: the `confirmed_gone` of its `group_stop` row
    (B2-C11 / `GroupStop`) and the cleanup disposition of its process-group target as the status
    frame projects it (`RunView.cleanup.processes`, B4-C7)."""
    report: dict[str, Any] = {}
    for row in records.ledger_rows(run_dir).rows:
        if row.get("kind") == "group_stop":
            report["confirmed_gone"] = row.get("confirmed_gone")
    home = run_dir.parents[2]
    kernel = harness.fresh_kernel(plugin_dirs=[], home=home)
    view = kernel.control.project.status(run_dir.name)
    cleanup = getattr(view, "cleanup", None)
    if cleanup is not None:
        report["cleanup"] = cleanup.processes
    return report


def _assert_unconfirmed_never_clean(run_dir: Path) -> None:
    report = _stop_report(run_dir)
    target_check(
        report.get("confirmed_gone") is False and report.get("cleanup") == "unknown",
        "G-A3",
        f"recovery reports the stop as {report!r}, not confirmed_gone=False with cleanup 'unknown'",
    )


@pytest.mark.proves("WR-CANCEL-3", LABEL, "core", "core", "PROC", "BOTH")
def test_target_s0_straddle_branch_i_unconfirmed_never_clean(tmp_path: Path) -> None:
    run_dir = straddle.straddle_run_dir(tmp_path)
    recover_run_dir(run_dir)
    assert records.node_record(run_dir).terminal == "interrupted"
    _assert_unconfirmed_never_clean(run_dir)


@pytest.mark.proves("WR-CANCEL-3", LABEL, "core", "core", "PROC", "BOTH")
def test_target_s0_orphan_not_signalled_branch_i_unconfirmed_never_clean(tmp_path: Path) -> None:
    run_dir = straddle.straddle_run_dir(tmp_path)
    with straddle.spawn_s0_shaped_orphan(run_dir) as orphan:
        recover_run_dir(run_dir)
        assert straddle.is_alive(orphan), "recovery signalled the S0-started orphan"
        assert records.node_record(run_dir).terminal == "interrupted"
        _assert_unconfirmed_never_clean(run_dir)
