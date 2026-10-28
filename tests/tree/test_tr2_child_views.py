"""L.TR-2.2: child views by (root run_id, path) through status (V-1.1, V-1.3, MC-B3-08).

A child is never its own run: its handle is derived from the root's run id and its canonical path,
its view is non-terminal while the root is live, and once the root is finalized (or recovered) it
carries the vertex's B4-C8 account, byte-equal to the root answer's. Every run directory here is
one `tests.tree.runs` planted through `AttemptLane` (TR-2 preamble)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from tests.fixtures.trees import generators
from tests.proof import harness
from tests.tree import runs
from trestle.common import codes
from trestle.common import lane_format as lf
from trestle.common.types import RequestOutcome, RunView
from trestle.server import answer
from trestle.server.ledger import RunLedger, evidence_dir, ledger_path
from trestle.server.main import Kernel
from trestle.server.recovery import recover_run_dir

proves_resolves = pytest.mark.proves(
    "WR-UNIT-2", "WR-UNIT-2:child-view-resolves", "A", "tree", "LOGIC", "BOTH"
)
proves_live = pytest.mark.proves(
    "WR-UNIT-7", "WR-UNIT-7:view-nonterminal-while-live", "A", "tree", "LOGIC", "BOTH"
)

DATA = ("data",)
DB = ("data", "db")
CACHE = ("data", "cache")
WEB = ("web",)


def _admit(kernel: Kernel) -> runs.TreeRun:
    return runs.admit(kernel, generators.fixture_tree("three_level"))


def _view(run: runs.TreeRun, path: tuple[str, ...]) -> RunView:
    handle = answer.child_handle(run.run_id, path)
    view = run.kernel.control.project.status(handle)
    assert isinstance(view, RunView), view
    return view


def _spec(run: runs.TreeRun) -> dict[str, Any]:
    return json.loads((evidence_dir(run.run_dir) / "spec.json").read_text(encoding="utf-8"))


def _root_answer(run: runs.TreeRun, terminal_kind: str) -> answer.TerminalAnswer:
    rows = RunLedger.open(ledger_path(run.run_dir)).records
    return answer.answer_for_run(run.run_dir, rows, terminal_kind, _spec(run))


def _finish(run: runs.TreeRun, lane: Any, *, failing: tuple[str, ...] | None = None) -> None:
    """Every vertex ends: leaves satisfied (`failing` blocked), composites rolled up."""
    leaves = set(run.leaves())
    for path in run.paths():
        if path in leaves and path == failing:
            end = runs.end(
                run,
                path,
                lf.Condition.BLOCKED,
                code="unit.blocked",
                human_action="Free port 80.",
                provenance=None,
            )
        elif path in leaves:
            end = runs.end(run, path)
        else:
            end = runs.end(run, path, None, provenance=None)
        assert lane.record_end(end) is None, path


@proves_resolves
def test_child_view_names_root_and_path(tree_kernel: Kernel) -> None:
    run = _admit(tree_kernel)
    other = _admit(tree_kernel)
    non_root = [p for p in run.paths() if p]
    assert len(non_root) == len(run.plan.vertices) - 1 == 4
    handles = {p: answer.child_handle(run.run_id, p) for p in non_root}
    assert len(set(handles.values())) == len(non_root)  # one handle per vertex
    for path, handle in handles.items():
        view = _view(run, path)
        assert (view.run_id, view.root_run_id, view.path) == (handle, run.run_id, "/".join(path))
        assert view.state == "queued"  # the root's own state (existing vocabulary)
        assert answer.child_handle(other.run_id, path) != handle  # the root is part of the identity
    # the derivation reads the plan and the root's id only: the same inputs, the same handle
    assert answer.child_paths(run.run_id, run.plan) == {h: p for p, h in handles.items()}
    # the root's own view is unchanged: no child key on its bytes
    root_view = tree_kernel.control.project.status(run.run_id)
    assert isinstance(root_view, RunView)
    assert not {"root_run_id", "path", "disposition"} & set(root_view.to_dict())
    # ... and a child view says it is one
    assert {"root_run_id", "path", "disposition"} <= set(_view(run, DB).to_dict())
    # the root is addressed by its run id, never by a child handle
    with pytest.raises(ValueError):
        answer.child_handle(run.run_id, ())
    # await_runs resolves child handles too, all of a batch
    views = tree_kernel.control.await_runs([answer.child_handle(run.run_id, DB)], timeout_ms=0)
    assert isinstance(views, list) and [v.path for v in views] == ["data/db"]


@proves_live
def test_child_view_nonterminal_while_root_live(tree_kernel: Kernel) -> None:
    run = _admit(tree_kernel)
    lane = runs.lane_of(run)
    _finish(run, lane)  # even with every vertex ended, the root is what decides
    view = _view(run, DB)
    assert view.state == "queued" and view.answer is None and view.disposition is None
    RunLedger.open(ledger_path(run.run_dir)).append("started", run_id=run.run_id)
    view = _view(run, DB)
    assert view.state == "running" and view.answer is None and view.disposition is None
    assert view.to_dict()["state"] == "running"
    harness.drive_tree(run.admitted)
    done = _view(run, DB)
    assert done.state not in {"queued", "running"} and done.answer is not None


@proves_resolves
@pytest.mark.parametrize("how", ["finalization", "recovery"])
def test_child_views_materialized_at_finalize_and_recovery(tree_kernel: Kernel, how: str) -> None:
    run = _admit(tree_kernel)
    lane = runs.lane_of(run)
    _finish(run, lane, failing=DB)
    if how == "finalization":
        harness.drive_tree(run.admitted)
    else:
        RunLedger.open(ledger_path(run.run_dir)).append("started", run_id=run.run_id)
        recover_run_dir(run.run_dir)
    ledger = RunLedger.open(ledger_path(run.run_dir))
    terminal = ledger.terminal_state()
    assert terminal is not None
    assert (evidence_dir(run.run_dir) / answer.CHILD_VIEWS_FILE).is_file()  # materialized
    rooted = _root_answer(run, terminal)
    for path in (p for p in run.paths() if p):
        view = _view(run, path)
        account = answer.account_of(rooted, path)
        assert account is not None, path
        assert view.answer == answer.node_wire(account), path  # B4-C8: equal by construction
        assert view.state == terminal
    # the failing leaf is the run's primary and its view says why
    failed = _view(run, DB)
    assert failed.answer is not None
    assert (failed.answer["condition"], failed.answer["code"]) == ("blocked", "unit.blocked")
    assert failed.answer["human_action"] == "Free port 80."


@proves_resolves
def test_never_started_child_disposition(tree_kernel: Kernel) -> None:
    run = _admit(tree_kernel)
    lane = runs.lane_of(run)
    # db: a NodeEnd that cut it (stopped); cache: never started (cut NOT_STARTED); web: entries
    # but no NodeEnd (unended); data and app: no NodeEnd at all
    assert lane.record_end(runs.end(run, DB, None, cut=lf.Cut.STOPPED)) is None
    assert lane.record_end(runs.end(run, CACHE, None, cut=lf.Cut.NOT_STARTED)) is None
    assert (
        lane.record_step(
            _step(run, WEB, lf.StepKind.NO_ACTION),
        )
        is None
    )
    RunLedger.open(ledger_path(run.run_dir)).append("started", run_id=run.run_id)
    recover_run_dir(run.run_dir)
    views = {p: _view(run, p) for p in (p for p in run.paths() if p)}
    assert views[DB].disposition == "stopped" and views[DB].answer["listing"] == "stopped"  # type: ignore[index]
    assert views[CACHE].disposition == "not_started"
    assert views[CACHE].answer is not None and views[CACHE].answer["node_class"] is None
    assert views[WEB].disposition == "unended"
    assert views[DATA].disposition == "not_started"  # no entry, no NodeEnd
    # and each equals the root answer's account of the vertex
    rooted = _root_answer(run, "interrupted")
    for path, view in views.items():
        account = answer.account_of(rooted, path)
        assert account is not None and view.answer == answer.node_wire(account), path


def _step(run: runs.TreeRun, path: tuple[str, ...], kind: lf.StepKind) -> lf.StepEntry:
    return lf.StepEntry(
        lineage=lf.Lineage(run.run_dir.name, path),
        at=runs.AT,
        kind=kind,
        code="unit_no_action",
        human_action=None,
        resend=None,
        handle=None,
    )


@proves_resolves
def test_composite_view_rolled_up_by_b4_c8(tree_kernel: Kernel) -> None:
    run = _admit(tree_kernel)
    lane = runs.lane_of(run)
    _finish(run, lane, failing=DB)
    harness.drive_tree(run.admitted)
    rooted = _root_answer(run, RunLedger.open(ledger_path(run.run_dir)).terminal_state() or "")
    composite = _view(run, DATA)
    assert composite.answer is not None
    assert composite.answer["listing"] == "rolled_up"  # `cut` and `condition` both None
    assert composite.answer["condition"] is None
    leaf = answer.account_of(rooted, DB)
    assert leaf is not None and leaf.node_class is not None
    # the key-min class over its subtree's candidates, and that candidate's code and action
    assert composite.answer["node_class"] == leaf.node_class.value
    assert composite.answer["code"] == "unit.blocked"
    assert composite.answer["human_action"] == "Free port 80."
    # a subtree with no candidate outcome but satisfied leaves rolls up to passed
    clean = _view(run, WEB)
    assert clean.answer is not None and clean.answer["listing"] == "candidate"


def test_unknown_child_handle_is_invalid(tree_kernel: Kernel) -> None:
    run = _admit(tree_kernel)
    project = tree_kernel.control.project
    for handle in (
        f"{run.run_id}~",
        f"{run.run_id}~{'0' * 24}",
        "~abc",
        answer.child_handle("r_nosuchroot", DB),
    ):
        out = project.status(handle)
        assert isinstance(out, RequestOutcome) and out.code == codes.INVALID_HANDLE, handle
