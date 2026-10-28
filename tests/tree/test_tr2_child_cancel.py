"""L.TR-2.5: a cancel addressed to a child handle is refused with `projection.cancel_not_root`
(OQ-27 assumed default, `WR-UNIT-6:child-addressed-cancel` gated_on OQ-27).

Its own leaf and commit, so a maintainer answer "act on the child" reverts exactly this leaf. The
MCP call and the "root run continuing to its own terminal class" assertion need an admitted tree
and are L.TR-L.9's (A2c2-4); here the run is admitted through the harness, its lane written through
`AttemptLane` (TR-2 preamble) and the refusal is read at `ControlSurface.cancel`."""

from __future__ import annotations

import argparse

import pytest

from tests.fixtures.trees import generators
from tests.proof import meta
from tests.tree import runs
from trestle.common import codes
from trestle.common.plan.compiler import REFUSAL_TEXT_MAX
from trestle.common.types import RequestOutcome
from trestle.server import answer
from trestle.server.ledger import RunLedger, ledger_path
from trestle.server.main import Kernel
from trestle.server.runs import cancel_flag_path

proves_cancel = pytest.mark.proves(
    "WR-UNIT-6", "WR-UNIT-6:child-addressed-cancel", "A", "tree", "LOGIC", "BOTH"
)


@proves_cancel
@pytest.mark.gated_on("OQ-27")
def test_child_cancel_assumed_refusal(tree_kernel: Kernel) -> None:
    run = runs.admit(tree_kernel, generators.fixture_tree("three_level"))
    lane = runs.lane_of(run)
    runs.write_all_ends(run, lane)
    ledger_bytes = ledger_path(run.run_dir).read_bytes()
    rows_before = RunLedger.open(ledger_path(run.run_dir)).records

    for path in (p for p in run.paths() if p):
        out = tree_kernel.control.cancel(answer.child_handle(run.run_id, path))
        assert isinstance(out, RequestOutcome)
        assert (
            (out.code, out.retryable, out.origin)
            == (
                codes.CANCEL_NOT_ROOT,
                False,
                "projection",
            )
            == ("projection.cancel_not_root", False, "projection")
        )
        assert run.run_id in out.message  # the root's run id
        assert len(out.message) <= REFUSAL_TEXT_MAX
        # no cancel flag was written, and the root's ledger is unchanged
        assert not cancel_flag_path(run.run_dir).exists()
        assert ledger_path(run.run_dir).read_bytes() == ledger_bytes
        assert RunLedger.open(ledger_path(run.run_dir)).records == rows_before

    # the root is still cancellable, and cancelling it is today's behavior (a flag, nothing else)
    accepted = tree_kernel.control.cancel(run.run_id)
    assert accepted.code == codes.CANCEL_ACCEPTED
    assert cancel_flag_path(run.run_dir).exists()


def test_child_cancel_keeps_todays_checks_first(tree_kernel: Kernel) -> None:
    """An unknown or forged handle stays `projection.invalid_handle`; a root that can no longer
    be cancelled is `invalid_handle` too, before the child refusal (B2-C12 layering)."""
    run = runs.admit(tree_kernel, generators.fixture_tree("three_level"))
    ghost = tree_kernel.control.cancel(answer.child_handle(run.run_id, ("data", "ghost")))
    assert ghost.code == codes.INVALID_HANDLE
    unknown = tree_kernel.control.cancel(answer.child_handle("r_nosuchroot", ("data", "db")))
    assert unknown.code == codes.INVALID_HANDLE
    ledger = RunLedger.open(ledger_path(run.run_dir))
    ledger.append("started", run_id=run.run_id)
    ledger.append("execution_ended", run_id=run.run_id, classification="failed", exit_code=1,
                  duration_ms=1)  # fmt: skip
    ledger.append(
        "evidence_finalized", run_id=run.run_id, completeness="partial", result_state="absent"
    )
    ledger.append("failed", run_id=run.run_id)
    done = tree_kernel.control.cancel(answer.child_handle(run.run_id, ("data", "db")))
    assert done.code == codes.INVALID_HANDLE and "not cancellable" in done.message


def test_open_question_lists_the_gated_claim() -> None:
    """The label the falsifier proves is bound to OQ-27 with posture `gated_on`, so `meta
    open-questions` (which fails on a bound label whose posture decides it) exits 0."""
    label = next(
        lb for lb in meta._load_all_labels() if lb["id"] == "WR-UNIT-6:child-addressed-cancel"
    )
    assert (label["posture"], label["oq"]) == ("gated_on", "OQ-27")
    assert meta.cmd_open_questions(argparse.Namespace()) == 0
