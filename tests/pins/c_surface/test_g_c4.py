"""G-C4 (BFD-25, D-i): preserve pins only, no target.

An undeclaring plugin gets a greedy fill of its result's fields in sorted-key
order that skips a field that does not fit and continues; an index over
`MAX_INDEX_BYTES` (64 KiB) coarsens the summary to a field count. The target
needs a declared summary field, which does not exist at S0, so it is written
as the first step of L.CL-B1.1 (DM-20): `test_target_declared_field_survives_budget`. The pins
carry no matrix `proves`; the target carries the clause it targets.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.pins.c_surface._support import as_view
from tests.proof import harness, tolerances
from tests.proof.markers import target_check
from trestle.child.index import MAX_INDEX_BYTES
from trestle.common.types import PublishView, RunView

GAP = "G-C4"
PLUGINS = Path(__file__).resolve().parent / "plugins"
FIELD_WIDTH = 200
FILL_BUDGET = 520  # fits two FIELD_WIDTH fields plus the small one, not three
INDEX_ENTRY_FLOOR = 16  # bytes: a lower bound on one index field entry


def sorted_fill_view() -> RunView:
    """Run `sorted_fill` under FILL_BUDGET and return its terminal frame."""
    kernel = harness.fresh_kernel([PLUGINS])
    with harness.patch_snapshot(kernel, "sorted_fill", summary_budget=FILL_BUDGET):
        return as_view(
            kernel.control.run(
                plugin="sorted_fill",
                args={"width": FIELD_WIDTH},
                wait_ms=tolerances.HARNESS_WAIT_MS,
            )
        )


@pytest.mark.pin(GAP)
def test_pin_sorted_greedy_skip_and_continue() -> None:
    view = sorted_fill_view()
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


DECLARED_FILL = """\
from trestle.plugin.surface import Context, trestle


@trestle(summary_fields=("status",))
def declared_fill(ctx: Context, width: int = 200) -> dict[str, str]:
    filler = "z" * width
    return {"aaa": filler, "bbb": filler, "ccc": filler, "status": "y" * (width - 50)}
"""


@pytest.mark.target(GAP)
@pytest.mark.proves(
    "WR-TERM-5", "WR-TERM-5:early-sorted-large-result-no-hide", "core", "core", "PROC", "CI"
)
@pytest.mark.xfail(strict=True, reason="defect:G-C4")
def test_target_declared_field_survives_budget(tmp_path: Path) -> None:
    """A declared `summary_fields` entry is reserved before the greedy fill, so a large
    early-sorted field cannot hide it. Alone it fits the budget; after two `FIELD_WIDTH` fields
    in sorted order it does not, so today's fill drops it."""
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    kernel = harness.fresh_kernel([plugin_dir], home=tmp_path / "home")
    assert isinstance(kernel.control.publish_plugin(DECLARED_FILL), PublishView)
    with harness.patch_snapshot(kernel, "declared_fill", summary_budget=FILL_BUDGET):
        view = as_view(
            kernel.control.run(
                plugin="declared_fill",
                args={"width": FIELD_WIDTH},
                wait_ms=tolerances.HARNESS_WAIT_MS,
            )
        )
    assert view.state == "succeeded"
    target_check(
        isinstance(view.summary, dict) and "status" in view.summary,
        GAP,
        f"declared summary field 'status' is absent from the summary "
        f"(summary keys {sorted(view.summary) if isinstance(view.summary, dict) else None}, "
        f"omitted {view.omitted})",
    )
