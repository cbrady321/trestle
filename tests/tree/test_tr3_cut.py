"""L.TR-3.5: an ordinary failed or blocked node cuts only its dependents (OQ-33).

On `failure_dependents` (`broken` fails or blocks; `left` and `right` need it; `independent` needs
nothing) no dependent has a start record, each is written `cut=NOT_STARTED` with no condition and
is listed `NOT_STARTED` in the answer (B4-T2 row 1), while `independent` runs to its own terminal
condition uncut. There is no whole-root stop for an ordinary failure: the goal stays CONVERGE, the
lane holds no stop, and (through a real run) the ledger holds no stop row."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from trestle_packs.fakes import FakeMarker

from tests.proof import harness, records
from tests.tree import treekit as tk
from trestle.common.outcome import OutcomeClass
from trestle.server.main import Kernel
from trestle.workflow.ports import ResourceCreate, ResourceOwned, ResourceReads

proves_dependents = pytest.mark.proves(
    "WR-UNIT-6", "WR-UNIT-6:ordinary-failure-dependents-only", "A", "tree", "PROC+MCP", "BOTH"
)
proves_reported = pytest.mark.proves(
    "WR-UNIT-7", "WR-UNIT-7:not-started-reported", "A", "tree", "LOGIC+MCP", "CI"
)

EXPECTED = {"failed": ("failed", "fixture.broke"), "blocked": ("blocked", "fixture.blocked")}


def _rig(tmp_path: Path, mode: str) -> tuple[tk.TreeRig, FakeMarker]:
    entry = tk.fixture_entry("failure_dependents")
    marker = FakeMarker(tmp_path / "markers", "run")
    fakes = {ResourceReads: marker, ResourceCreate: marker, ResourceOwned: marker}
    rig = tk.rig_of_entry(
        tmp_path / "run",
        entry,
        port_impl=fakes,
        request={"env": "dev", "mode": mode},
        keep_units=True,
    )
    return rig, marker


def _walked(tmp_path: Path, mode: str) -> tk.TreeRig:
    rig, marker = _rig(tmp_path, mode)
    try:
        rig.run()
    finally:
        marker.close()
    return rig


@proves_dependents
@pytest.mark.parametrize("mode", ["failed", "blocked"])
def test_failed_branch_stops_only_dependents(tmp_path: Path, mode: str) -> None:
    rig = _walked(tmp_path, mode)
    rows, ends = rig.rows(), rig.ends()
    condition, code = EXPECTED[mode]
    assert (ends["broken"]["condition"], ends["broken"]["code"]) == (condition, code)
    assert ends["broken"]["cut"] is None  # it reached its own condition
    # no dependent has a start record: its only record is its own NodeEnd
    for dependent in ("left", "right"):
        mine = [row for row in rows if row.get("path") == dependent]
        assert [row["class"] for row in mine] == ["end"], dependent
        assert (mine[0]["condition"], mine[0]["code"], mine[0]["cut"]) == (
            None,
            None,
            "not_started",
        )
    # the whole-root goal never flipped for an ordinary failure: nothing else was cut
    assert ends["independent"]["cut"] is None and ends[""]["cut"] is None


@proves_dependents
def test_independent_sibling_uncut(tmp_path: Path) -> None:
    for mode in ("failed", "blocked"):
        rig = _walked(tmp_path / mode, mode)
        rows, ends = rig.rows(), rig.ends()
        assert (ends["independent"]["condition"], ends["independent"]["cut"]) == (
            "satisfied",
            None,
        )
        # its fake step ran (the create was applied) and was released afterwards, in full
        confirmed = [r for r in rows if r["class"] == "confirmation" and r["path"] == "independent"]
        assert [r["effect"] for r in confirmed] == ["up", "stop"]
        assert all(r["status"] == "applied" for r in confirmed)


@proves_reported
@pytest.mark.parametrize("mode", ["failed", "blocked"])
def test_unstarted_dependents_not_started(tmp_path: Path, mode: str) -> None:
    rig = _walked(tmp_path, mode)
    got = tk.answer_of(rig)
    assert got.outcome is (OutcomeClass.FAILED if mode == "failed" else OutcomeClass.BLOCKED)
    assert got.primary.path == ("broken",)
    listed = {tuple(item.path): item.listing.value for item in got.listed}
    assert listed[("left",)] == listed[("right",)] == "not_started"  # never passed or failed
    assert listed[("independent",)] == "candidate"  # it reached its own condition: not cut
    assert got.root_stop is None  # no whole-root stop row for an ordinary failure


@proves_dependents
@pytest.mark.parametrize("mode", ["failed", "blocked"])
def test_no_stop_row_through_a_real_run(tree_kernel: Kernel, tmp_path: Path, mode: str) -> None:
    """The same tree as a real run (a kernel, a wrapper and a child process): the ledger holds no
    stop row, the dependents are `not_started` in the answer and `independent` is satisfied."""
    path = tmp_path / "plugins" / "failure_dependents.py"
    path.write_text(tk.fixture_source("failure_dependents"), encoding="utf-8")
    admitted = harness.admit_tree(path, {"env": "dev", "mode": mode}, kernel=tree_kernel)
    view = harness.drive_tree(admitted)
    stop_rows = [
        r for r in records.ledger_rows(admitted.run_dir).rows if r.get("kind") == "stop_row"
    ]
    assert stop_rows == []
    ends = tk.ends_by_path(admitted.run_dir)
    assert ends["left"]["cut"] == ends["right"]["cut"] == "not_started"
    assert (ends["independent"]["condition"], ends["independent"]["cut"]) == ("satisfied", None)
    answer: dict[str, Any] = view.to_dict()["answer"]
    assert answer["root_stop"] is None
    assert answer["outcome"] == mode
    listed = {tuple(item["path"]): item["listing"] for item in answer["listed"]}
    assert listed[("left",)] == listed[("right",)] == "not_started"
