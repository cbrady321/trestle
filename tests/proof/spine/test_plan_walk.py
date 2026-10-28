"""The plan-walk sub-suite (L.SV-5.10; MC-26 full): the one-vertex fixture is compiled and admitted
by `harness.run_tree` (through `write_admitted_run`, MC-B2-08), run to terminal by the product's own
conductor and child, and read back through the proof court's seam alone (`tests.proof.records`, the
independent strict lane oracle). What is proven is about the record the run left:

* the vertices it started are vertices of the admitted plan ({()} at one vertex);
* its plan entry (B2 `PlanIdentity`, written by `record_plan`) is the lane's first entry and
  precedes every claim (B1-I8);
* the plan the loop walked is the plan admission accepted (B2-C3): its digests verify;
* a stale declaration digest, or a plan whose digest no longer covers it, is `DECLARATION_STALE`
  with zero tickets (B1-E7);
* an entry for a path that is not a vertex makes the run's cleanup `unknown` (B2 `FoldedRecord`);
* the plan identity is independent of argument order and catalog order (A2.1).

Every timing bound is the harness's; no timing literal appears here (SA-05).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.proof import harness, records
from trestle.common import codes
from trestle.common.plan import formats
from trestle.common.plan.compiler import AdmittedPlan
from trestle.server import fold

pytestmark = pytest.mark.spine

REPO = Path(__file__).resolve().parents[3]
FIXTURE = REPO / "tests" / "fixtures" / "workflows" / "spine_leaf.py"
ADVANCE = {"env": "dev", "mode": "advance"}


def _spec(run_dir: Path) -> dict[str, Any]:
    loaded = json.loads((run_dir / "evidence" / "spec.json").read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _write_spec(run_dir: Path, spec: dict[str, Any]) -> None:
    (run_dir / "evidence" / "spec.json").write_text(json.dumps(spec), encoding="utf-8")


def _classes(run_dir: Path) -> list[str]:
    lane = records.lane_rows(run_dir)
    assert not lane.problems and not lane.torn, lane.problems
    return [row.cls for row in lane.rows]


def plan_before_claim(classes: list[str]) -> list[str]:
    """The oracle's own check of B1-I8 over a lane's class sequence: one plan entry, first, and
    before every claim (`issue`) and every other entry. Returns the violations."""
    violations: list[str] = []
    if classes.count("plan") != 1:
        violations.append(f"{classes.count('plan')} plan entries")
    if classes and classes[0] != "plan":
        violations.append(f"the first entry is {classes[0]}, not the plan")
    return violations


# ---- the walk ---------------------------------------------------------------------------------


def test_started_paths_are_plan_vertices() -> None:
    run_dir = harness.run_tree(FIXTURE, ADVANCE)
    vertices = {v["path"] for v in _spec(run_dir)["plan"]["vertices"]}
    assert vertices == {""}  # the root: path (), one vertex
    started = {row.path for row in records.lane_rows(run_dir).rows if row.cls in ("issue", "end")}
    assert started and started <= vertices, started


def test_plan_entry_precedes_first_ticket() -> None:
    run_dir = harness.run_tree(FIXTURE, ADVANCE)
    classes = _classes(run_dir)
    assert "issue" in classes, classes
    assert plan_before_claim(classes) == []
    assert classes.index("plan") < classes.index("issue")


def test_walked_plan_digest_equals_spec_plan_digest() -> None:
    run_dir = harness.run_tree(FIXTURE, ADVANCE)
    spec = _spec(run_dir)["plan"]
    # the plan the loop walked is the plan admission accepted: it verifies (B2-C3) ...
    plan = AdmittedPlan.from_json(json.dumps(spec))
    assert plan.plan_digest == spec["plan_digest"]
    assert formats.plan_digest(plan.body()) == spec["plan_digest"]
    # ... and the plan identity the loop recorded names the declaration that plan was built from
    (entry,) = [row.entry for row in records.lane_rows(run_dir).rows if row.cls == "plan"]
    assert entry["declaration_digest"] == spec["declaration_digest"] == plan.declaration_digest


# ---- planted digests --------------------------------------------------------------------------


def test_planted_declaration_digest_mismatch_is_stale_with_zero_tickets() -> None:
    """The plan verifies as a plan but was built from another declaration than the one `declare()`
    yields now: the loop stops before any effect and the root's NodeEnd carries the code."""
    admitted = harness.admit_tree(FIXTURE, ADVANCE)
    spec = _spec(admitted.run_dir)
    body = {k: v for k, v in spec["plan"].items() if k != "plan_digest"}
    body["declaration_digest"] = "0" * len(body["declaration_digest"])
    spec["plan"] = {**body, "plan_digest": formats.plan_digest(body)}
    _write_spec(admitted.run_dir, spec)
    view = harness.drive_tree(admitted)
    classes = _classes(admitted.run_dir)
    assert classes == ["plan", "end"], classes
    (end,) = [row.entry for row in records.lane_rows(admitted.run_dir).rows if row.cls == "end"]
    assert (end["condition"], end["code"], end["path"]) == ("failed", codes.DECLARATION_STALE, "")
    answer = view.to_dict()["answer"]
    assert answer["outcome"] == "execution_error", answer
    assert answer["error"]["code"] == codes.DECLARATION_STALE, answer


def test_planted_plan_digest_mismatch_is_stale_with_zero_tickets() -> None:
    """The plan's digest no longer covers it: the child refuses it before any plugin code runs
    (B1-E7 through B2-C3), so no lane is opened and no ticket exists. The loop's own re-check of
    the same digest is `test_loop.py::test_plan_digest_mismatch_declaration_stale_zero_effects`
    (a NodeEnd needs the loop, and the loop is never reached here)."""
    admitted = harness.admit_tree(FIXTURE, ADVANCE)
    spec = _spec(admitted.run_dir)
    spec["plan"]["release_slice"] = spec["plan"]["release_slice"] + 1  # digest no longer covers it
    _write_spec(admitted.run_dir, spec)
    view = harness.drive_tree(admitted)
    assert _classes(admitted.run_dir) == []  # zero tickets: nothing was ever written
    error = view.to_dict()["error"]
    assert error["code"] == codes.DECLARATION_STALE, error
    assert view.state != "succeeded", view


# ---- A2.1: plan identity is independent of argument and catalog order -----------------------


def _plan_entry_bytes(run_dir: Path) -> bytes:
    """The lane's plan entry, byte for byte."""
    data = (run_dir / "evidence" / "lane.ndjson").read_bytes()
    (row,) = [r for r in records.lane_rows(run_dir).rows if r.cls == "plan"]
    return data[row.offset : row.end]


@pytest.mark.proves("A2.1", "A2.1", "A", "single", "LOGIC+PROC", "CI")
def test_selection_entry_equal_under_permuted_args_and_catalog(tmp_path: Path) -> None:
    """A2.1's second registered node (beside the admission-time digest node): the plan entry the
    loop wrote is byte-equal across two runs whose arguments and plugin catalog are the same
    values in another order (WR-PLAN-1: permuting input or catalog order changes no identity)."""
    echo = harness.DEFAULT_PLUGIN_DIR  # a second plugin in the catalog, before or after the fixture
    first = harness.run_tree(
        FIXTURE,
        {"env": "dev", "mode": "skip"},
        kernel=harness.fresh_kernel([_catalog(tmp_path / "a"), echo]),
    )
    second = harness.run_tree(
        FIXTURE,
        {"mode": "skip", "env": "dev"},
        kernel=harness.fresh_kernel([echo, _catalog(tmp_path / "b")]),
    )
    assert _plan_entry_bytes(first) == _plan_entry_bytes(second)
    assert _spec(first)["plan"]["plan_digest"] == _spec(second)["plan"]["plan_digest"]


def _catalog(directory: Path) -> Path:
    """A plugin directory holding the fixture."""
    directory.mkdir(parents=True)
    (directory / FIXTURE.name).write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    return directory


# ---- a non-vertex path ------------------------------------------------------------------------


def test_planted_non_vertex_path_is_unknown_and_cleanup_unknown() -> None:
    """An entry for a path the plan does not list (a NodeEnd included) is reported in
    `unknown_paths` and makes cleanup `unknown`, never clean (B2 `FoldedRecord`, B2-C9)."""
    admitted = harness.admit_tree(FIXTURE, {"env": "dev", "mode": "skip"})
    harness.drive_tree(admitted)
    lane = admitted.run_dir / "evidence" / "lane.ndjson"
    rows = records.lane_rows(admitted.run_dir).rows
    ghost = {
        "class": "end",
        "seq": rows[-1].entry["seq"] + 1,
        "path": "ghost",
        "at": rows[-1].entry["at"],
        "condition": "failed",
        "code": "unit.raised",
        "human_action": None,
        "resend": None,
        "provenance": None,
        "cut": None,
    }
    with lane.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(ghost, separators=(",", ":")) + "\n")
    seen = records.lane_rows(admitted.run_dir)
    assert not seen.problems and seen.rows[-1].path == "ghost"
    accepted = fold.plan_of_spec(_spec(admitted.run_dir))
    folded = fold.fold_lane(admitted.run_dir, accepted)
    assert folded.unknown_paths == ("ghost",)
    assert fold.cleanup_is_unknown(folded)
    # the answer the host recomputes from the durable inputs says the same
    view = admitted.kernel.control.project.status(admitted.run_id)
    assert not isinstance(view, harness.RequestOutcome)
    assert view.to_dict()["answer"]["cleanup"]["clean"] is False


# ---- the oracle catches a claim before the plan --------------------------------------------


def test_planted_claim_before_plan_caught(tmp_path: Path) -> None:
    """Not vacuous: a real lane passes, and the same lane with a claim moved before its plan
    entry (a defect planted into a copy of the file) is caught."""
    run_dir = harness.run_tree(FIXTURE, ADVANCE)
    lane = records.lane_rows(run_dir)
    assert plan_before_claim([row.cls for row in lane.rows]) == []
    entries = [row.entry for row in lane.rows]
    (issue_at,) = [
        i for i, e in enumerate(entries) if e["class"] == "issue" and e["effect"] == "up"
    ]
    planted = [entries[issue_at], *entries[:issue_at], *entries[issue_at + 1 :]]
    copy = tmp_path / "planted-run"
    (copy / "evidence").mkdir(parents=True)
    text = "".join(
        json.dumps({**e, "seq": n}, separators=(",", ":")) + "\n"
        for n, e in enumerate(planted, start=1)
    )
    (copy / "evidence" / "lane.ndjson").write_text(text, encoding="utf-8")
    assert plan_before_claim(_classes(copy)) != []
    assert plan_before_claim(["plan", "plan", "issue"]) != []
