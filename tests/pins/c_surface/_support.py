"""Shared helpers for the lane-C pins (imports only `tests.proof.*`)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tests.proof import tolerances
from trestle.common.types import RunView
from trestle.server.ledger import run_dir_for
from trestle.server.main import Kernel

# A whole-second run budget: one harness settle unit (no timing literal in
# a pin, SA-05).
BUDGET_S = int(tolerances.SETTLE_LONG_S)
BUDGET_MS = BUDGET_S * 1000


def as_view(result: Any) -> RunView:
    assert isinstance(result, RunView), result
    return result


def settle(kernel: Kernel, run_id: str) -> RunView:
    """Wait (bounded by harness patience) until the run is terminal."""
    return as_view(kernel.control.project.await_one(run_id, tolerances.HARNESS_WAIT_MS))


def run_dir(kernel: Kernel, run_id: str) -> Path:
    return run_dir_for(kernel.home, run_id)
