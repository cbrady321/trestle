"""L.TR-3.1: the run-start proof covers every descendant declaration.

The child re-derives the declared tree over descendants and compares it with the admitted MC-34
digest before the walk and before the first ticket (B2-C3; B1-E7). A mismatch stops the run: the
root's `NodeEnd` is `FAILED` `DECLARATION_STALE`, every other vertex of `V_run` is written
`cut=NOT_STARTED`, and no ticket or port call happens. The proof precedes the walk, so it holds
while the composite raise (TM-B2-6) still stands."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.proof import harness, records
from tests.tree import treekit
from trestle.common import codes
from trestle.server.ledger import evidence_dir
from trestle.server.main import Kernel

pytestmark = pytest.mark.proves(
    "WR-UNIT-8", "WR-UNIT-8:descendant-edit-running-root-unchanged", "A", "tree", "LOGIC", "CI"
)


def _publish(kernel: Kernel, tmp_path: Path, name: str) -> Path:
    path = tmp_path / "plugins" / f"{name}.py"
    path.write_text(treekit.runnable_source(treekit.fixture_source(name)), encoding="utf-8")
    return path


def _events(run_dir: Path) -> list[dict[str, object]]:
    path = evidence_dir(run_dir) / "events.ndjson"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _edit_snapshot(kernel: Kernel, run_dir: Path, old: str, new: str) -> None:
    """Edit the snapshot the run executes, after admission: the declaration `declare()` yields now
    is no longer the one the admitted digest covers."""
    spec = json.loads((evidence_dir(run_dir) / "spec.json").read_text(encoding="utf-8"))
    plugin = kernel.home / "snapshots" / spec["snapshot_id"] / "plugin.py"
    text = plugin.read_text(encoding="utf-8")
    assert old in text
    plugin.write_text(text.replace(old, new), encoding="utf-8")


def test_descendant_decl_changed_after_admission_stops_zero_effects(
    tree_kernel: Kernel, tmp_path: Path
) -> None:
    fixture = _publish(tree_kernel, tmp_path, "three_level")
    admitted = harness.admit_tree(fixture, kernel=tree_kernel)
    # `db` is two levels below the root (app / data / db): a descendant, not a root edit
    _edit_snapshot(
        tree_kernel, admitted.run_dir, '"db": leaf("db"),', '"db": leaf("db", budget=31),'
    )
    view = harness.drive_tree(admitted)

    lane = records.lane_rows(admitted.run_dir)
    assert not lane.problems and not lane.torn, lane.problems
    classes = [row.cls for row in lane.rows]
    assert classes == ["plan"] + ["end"] * 5, classes  # zero tickets, zero steps
    ends = treekit.ends_by_path(admitted.run_dir)
    assert set(ends) == {"", "data", "data/db", "data/cache", "web"}
    root = ends[""]
    assert (root["condition"], root["code"], root["cut"]) == (
        "failed",
        codes.DECLARATION_STALE,
        None,
    )
    for path, end in ends.items():
        if path != "":
            assert (end["condition"], end["code"], end["cut"]) == (None, None, "not_started"), path
    # zero port calls, zero step facts: the loop reported nothing but the stop
    assert not [e for e in _events(admitted.run_dir) if str(e.get("kind", "")).startswith("step.")]
    answer = view.to_dict()["answer"]
    assert answer["outcome"] == "execution_error", answer
    assert answer["error"]["code"] == codes.DECLARATION_STALE, answer


def test_unedited_descendants_pass_the_proof(tree_kernel: Kernel, tmp_path: Path) -> None:
    """The proof is a comparison, not a refusal of every tree: with nothing edited the run gets
    past it to the walk (the composite raise, which is TR-3.2's to remove, is then the loop's)."""
    fixture = _publish(tree_kernel, tmp_path, "three_level")
    admitted = harness.admit_tree(fixture, kernel=tree_kernel)
    harness.drive_tree(admitted)
    ends = treekit.ends_by_path(admitted.run_dir)
    assert not any(end["code"] == codes.DECLARATION_STALE for end in ends.values())
