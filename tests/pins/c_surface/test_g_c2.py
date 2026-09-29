"""G-C2 (D-k): the summary budget is read from the current snapshot, not
the run's own spec.

Pin: republishing a plugin with a new budget changes the projection of an
already-admitted run. Target: the budget follows the run's spec
(`evidence/spec.json` `summary_budget`), whatever is published later.
"""

from __future__ import annotations

import pytest

from tests.pins.c_surface._support import as_view, run_dir
from tests.proof import harness, tolerances
from tests.proof.markers import target_check
from trestle.common.types import RunView

GAP = "G-C2"
ELEMENTS = 200  # ~1.1 KB result: over the tight budget, under the roomy one
TIGHT_BUDGET = 256
ROOMY_BUDGET = 65536


def _project_across_republish() -> tuple[RunView, RunView]:
    """Admit `big_array` under TIGHT_BUDGET, finish it, then republish the
    plugin with ROOMY_BUDGET and project the same run again."""
    kernel = harness.fresh_kernel()
    with harness.patch_snapshot(kernel, "big_array", summary_budget=TIGHT_BUDGET):
        admitted = as_view(
            kernel.control.run(
                plugin="big_array",
                args={"count": ELEMENTS},
                wait_ms=tolerances.HARNESS_WAIT_MS,
            )
        )
    assert (run_dir(kernel, admitted.run_id) / "evidence" / "spec.json").is_file()
    with harness.patch_snapshot(kernel, "big_array", summary_budget=ROOMY_BUDGET):
        after = as_view(kernel.control.project.status(admitted.run_id))
    return admitted, after


@pytest.mark.pin(GAP)
def test_pin_budget_follows_current_snapshot() -> None:
    admitted, after = _project_across_republish()
    assert admitted.state == "succeeded"
    assert admitted.truncated is True  # tight budget at admission
    assert after.truncated is False  # the later roomy budget re-projected it


@pytest.mark.target(GAP)
@pytest.mark.proves("WR-TERM-5", "WR-TERM-5:budget-from-own-spec", "core", "core", "PROC", "CI")
@pytest.mark.xfail(strict=True, reason="defect:G-C2")
def test_target_budget_follows_spec() -> None:
    admitted, after = _project_across_republish()
    target_check(
        after.truncated == admitted.truncated and after.omitted == admitted.omitted,
        GAP,
        "projection of an admitted run changed when the plugin was republished "
        "with a different summary budget",
    )
