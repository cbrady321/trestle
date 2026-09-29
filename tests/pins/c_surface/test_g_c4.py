"""G-C4 (BFD-25, D-i): preserve pins only, no target.

An undeclaring plugin gets a greedy fill of its result's fields in sorted-key
order that skips a field that does not fit and continues; an index over
`MAX_INDEX_BYTES` (64 KiB) coarsens the summary to a field count. The target
needs a declared summary field, which does not exist at S0, so it is written
as the first step of L.CL-B1.1 (DM-20). These pins carry no matrix `proves`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.pins.c_surface._support import as_view
from tests.proof import harness, tolerances
from trestle.child.index import MAX_INDEX_BYTES

GAP = "G-C4"
PLUGINS = Path(__file__).resolve().parent / "plugins"
FIELD_WIDTH = 200
FILL_BUDGET = 520  # fits two FIELD_WIDTH fields plus the small one, not three
INDEX_ENTRY_FLOOR = 16  # bytes: a lower bound on one index field entry


@pytest.mark.pin(GAP)
def test_pin_sorted_greedy_skip_and_continue() -> None:
    kernel = harness.fresh_kernel([PLUGINS])
    with harness.patch_snapshot(kernel, "sorted_fill", summary_budget=FILL_BUDGET):
        view = as_view(
            kernel.control.run(
                plugin="sorted_fill",
                args={"width": FIELD_WIDTH},
                wait_ms=tolerances.HARNESS_WAIT_MS,
            )
        )
    assert view.state == "succeeded"
    assert view.truncated is True
    # k1, k2 take the budget in sorted order; k3 does not fit and is skipped;
    # the later, small k4 is still taken (skip-and-continue).
    assert sorted(view.summary) == ["k1", "k2", "k4"]
    assert view.omitted == ["k3"]
    assert view.next is not None


@pytest.mark.pin(GAP)
def test_pin_index_coarsened_summary_is_field_count() -> None:
    count = MAX_INDEX_BYTES // INDEX_ENTRY_FLOOR
    kernel = harness.fresh_kernel([PLUGINS])
    view = as_view(
        kernel.control.run(
            plugin="many_fields",
            args={"count": count},
            wait_ms=tolerances.HARNESS_WAIT_MS,
        )
    )
    assert view.state == "succeeded"
    assert view.truncated is True
    assert view.summary == {"field_count": count}
    assert view.omitted == ["*"]
    assert view.next is not None
