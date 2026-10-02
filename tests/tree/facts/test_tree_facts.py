"""L.TR-6.5: record facts of a multi-vertex run with the plugin dead (MC-10 is the only format
reader, SA-06).

Every check here reads the run directory alone, through the proof court's own lane oracle
(`tests.proof.records`, MC-10) and the host's projections over it; nothing calls the plugin. The
runs are *real* (a kernel admits a fixture tree, a wrapper and a child process walk it, the
child exits: `support.marked(run_id)` is empty when a fact is read) or *planted* (a run
directory whose lane the test wrote through `AttemptLane`, then finalized or recovered, TR-2
preamble). Four facts:

* started paths are vertices (WR-UNIT-2): every lane entry that says a node acted names a vertex
  of the admitted plan (`spec.json`'s plan); a start at a non-vertex path is detected;
* a child's view is its account in the root's answer (WR-UNIT-7, B4-C8): byte-equal, for every
  non-root vertex;
* nothing under the run changes once it is finalized (WR-UNIT-7 views, C-RECORD-INTEGRITY): each
  vertex's slice of the lane, every evidence file and every result file hash the same at
  finalization and after every view is read and recovery is run again;
* the same with a survivor (WR-CANCEL-2:tree, PROC, BOTH): a child whose descendants ignore SIGTERM
  and keep appending to the run's evidence and rewriting its result is stopped by the root
  deadline, and the bytes hashed at the terminal row equal those sampled at the end of the
  observation window after it (MC-09), with no attributable survivor (MC-13)."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from tests.core.spine import support
from tests.fixtures.trees import generators
from tests.proof import ancestry, harness, records, tolerances
from tests.single.record import support as sup
from tests.tree import runs
from tests.tree import treekit as tk
from trestle.common import clock
from trestle.common import lane_format as lf
from trestle.common.fsutil import append_ndjson
from trestle.common.types import RunView
from trestle.server import answer, fold
from trestle.server.ledger import RunLedger, evidence_dir, ledger_path
from trestle.server.main import Kernel
from trestle.server.procident import GroupStop
from trestle.server.recovery import recover_run_dir

proves_started = pytest.mark.proves(
    "WR-UNIT-2", "WR-UNIT-2:started-paths-are-vertices", "A", "tree", "MCP+LOGIC", "CI"
)
proves_view = pytest.mark.proves(
    "WR-UNIT-7", "WR-UNIT-7:child-view-equals-root", "A", "tree", "LOGIC+MCP", "CI"
)
proves_frozen = pytest.mark.proves(
    "WR-UNIT-7", "WR-UNIT-7:views-terminal-evidence-frozen", "A", "tree", "LOGIC+MCP", "CI"
)
proves_survivor = [
    pytest.mark.proves("WR-CANCEL-2", "WR-CANCEL-2:tree", "A", "tree", "PROC", "BOTH"),
    pytest.mark.proves(
        "C-RECORD-INTEGRITY", "C-RECORD-INTEGRITY:child-slice-frozen", "A", "tree", "PROC", "BOTH"
    ),
]

# Real multi-vertex runs read below: an exception (a whole-root stop, every leaf below cut) and an
# ordinary failure (dependents cut, an independent leaf finishes), each admitted with `env`.
REAL = ("exception_branch", "failure_dependents")

# The files a finished run leaves as its result (B2-C10's finalization), all under evidence/.
RESULT_FILES = ("result.json", "result.index", "answer.json", "child_views.json", "summary.json")

DATA = ("data",)
DB = ("data", "db")


# ---- reading a run directory, plugin dead


def _spec(run_dir: Path) -> dict[str, Any]:
    spec: dict[str, Any] = json.loads(
        (evidence_dir(run_dir) / "spec.json").read_text(encoding="utf-8")
    )
    return spec


def vertices_of(run_dir: Path) -> set[str]:
    """The encoded path of every vertex of the plan the run was admitted with."""
    plan = fold.plan_of_spec(_spec(run_dir))
    assert plan is not None
    return {v.path for v in plan.vertices}


def started_paths(run_dir: Path) -> set[str]:
    """The paths the lane says acted: every entry but the plan's, and but the `NodeEnd` of a
    vertex that never started (cut `not_started`, which is the end of a node that did nothing)."""
    lane = records.lane_rows(run_dir)
    assert not lane.problems and not lane.torn and lane.unknown == 0, lane.problems
    out: set[str] = set()
    for row in lane.rows:
        if row.cls == "plan":
            continue
        if row.cls == "end" and row.entry.get("cut") == "not_started":
            continue
        assert row.path is not None, row.entry
        out.add(row.path)
    return out


def non_vertices(run_dir: Path) -> set[str]:
    """Started paths that are no vertex of the admitted plan (empty on every honest run)."""
    return started_paths(run_dir) - vertices_of(run_dir)


def ends_of(run_dir: Path) -> dict[str, dict[str, Any]]:
    lane = records.lane_rows(run_dir)
    out: dict[str, dict[str, Any]] = {}
    for row in lane.rows:
        if row.cls == "end":
            assert row.path not in out, f"a second NodeEnd for {row.path!r}"
            out[str(row.path)] = row.entry
    return out


@dataclass(frozen=True)
class Frozen:
    """Everything under a finished run that a survivor could change: each node's slice of the
    lane (its accepted entries' bytes, in file order), every evidence file, and the result files."""

    slices: dict[str, str]
    evidence: dict[str, str]
    results: dict[str, str]


VANISHED = "<listed, then gone before its read>"


def freeze(run_dir: Path) -> Frozen:
    evidence = evidence_dir(run_dir)
    lane_bytes = (evidence / "lane.ndjson").read_bytes()
    grouped: dict[str, bytearray] = {}
    for row in records.lane_rows(run_dir).rows:
        grouped.setdefault(row.path if row.path is not None else "<plan>", bytearray()).extend(
            lane_bytes[row.offset : row.end]
        )
    files: dict[str, str] = {}
    for path in sorted(evidence.rglob("*")):
        # A live writer's temporary file (`.result_<name>.part`, renamed into place) can be listed
        # and gone before it is read (CI run 36955650845, CK-8). That is a change under the read,
        # recorded as such, never an error: the negative control counts it, the frozen check fails.
        try:
            if path.is_file():
                files[str(path.relative_to(evidence))] = hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
        except FileNotFoundError:
            files[str(path.relative_to(evidence))] = VANISHED
    return Frozen(
        slices={path: hashlib.sha256(data).hexdigest() for path, data in grouped.items()},
        evidence=files,
        results={name: files[name] for name in RESULT_FILES if name in files},
    )


def observe_window(run_dir: Path) -> list[Frozen]:
    """`Frozen` sampled across the observation window after the terminal row (MC-09)."""
    seen: list[Frozen] = []
    end = time.monotonic() + tolerances.SETTLE_LONG_S * 2
    while time.monotonic() < end:
        seen.append(freeze(run_dir))
        time.sleep(tolerances.POLL_S)
    return seen


# ---- runs


@dataclass(frozen=True)
class Finished:
    """A finalized run and the kernel that ran it."""

    name: str
    kernel: Kernel
    run_id: str
    run_dir: Path
    terminal: str


@pytest.fixture(scope="module")
def real_runs(tmp_path_factory: pytest.TempPathFactory) -> Iterator[list[Finished]]:
    """`REAL` fixtures run through one kernel, wrapper and child process each; every run is
    finished, its process tree gone, before a test reads it."""
    patch = pytest.MonkeyPatch()
    base = tmp_path_factory.mktemp("tree_facts")
    plugins = base / "plugins"
    plugins.mkdir()
    home = base / "home"
    patch.setenv("TRESTLE_HOME", str(home))
    patch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    try:
        kernel = harness.fresh_kernel([plugins], home=home)
        out: list[Finished] = []
        for name in REAL:
            path = plugins / f"{name}.py"
            path.write_text(tk.fixture_source(name), encoding="utf-8")
            admitted = harness.admit_tree(path, {"env": "dev"}, kernel=kernel)
            with support.reaping(admitted.run_id):
                harness.drive_tree(admitted)
                terminal = RunLedger.open(ledger_path(admitted.run_dir)).terminal_state()
                assert terminal is not None, name
                assert not support.marked(admitted.run_id), f"{name}: the plugin is not dead"
            out.append(Finished(name, kernel, admitted.run_id, admitted.run_dir, terminal))
        yield out
    finally:
        patch.undo()


def planted_run(kernel: Kernel, how: str = "finalize") -> Finished:
    """`three_level` with a planted lane (db blocked, the rest satisfied) finalized in-library
    (`finalize`) or recovered (`recover`): the plugin function returns at once, never asked."""
    run = runs.admit(kernel, generators.fixture_tree("three_level"))
    lane = runs.lane_of(run)
    leaves = set(run.leaves())
    for path in run.paths():
        if path == DB:
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
    if how == "finalize":
        harness.drive_tree(run.admitted)
    else:
        RunLedger.open(ledger_path(run.run_dir)).append("started", run_id=run.run_id)
        recover_run_dir(run.run_dir)
    terminal = RunLedger.open(ledger_path(run.run_dir)).terminal_state()
    assert terminal is not None
    return Finished(f"three_level/{how}", kernel, run.run_id, run.run_dir, terminal)


def _view(finished: Finished, path: tuple[str, ...]) -> RunView:
    handle = answer.child_handle(finished.run_id, path)
    view = finished.kernel.control.project.status(handle)
    assert isinstance(view, RunView), view
    return view


def _paths(finished: Finished) -> list[tuple[str, ...]]:
    return [tuple(p.split("/")) for p in sorted(vertices_of(finished.run_dir)) if p]


# ---- started paths are vertices


@proves_started
def test_started_paths_are_vertices(real_runs: list[Finished], tree_kernel: Kernel) -> None:
    """On every real run, read with the plugin dead, each path that carries a lane entry is a
    vertex of the admitted plan, every vertex has exactly one `NodeEnd`, and a vertex that never
    started ends `not_started` with nothing under it in the lane. The same on a planted run."""
    finished = [*real_runs, planted_run(tree_kernel)]
    assert len(finished) == len(REAL) + 1
    for run in finished:
        vertices = vertices_of(run.run_dir)
        assert len(vertices) >= 4, run.name  # a multi-vertex run: read from the plan
        started = started_paths(run.run_dir)
        assert started, run.name  # something started
        assert non_vertices(run.run_dir) == set(), run.name
        assert started <= vertices, run.name
        ends = ends_of(run.run_dir)
        assert set(ends) == vertices, run.name  # V-4.8: one NodeEnd per vertex of the walked set
        for path, end in ends.items():
            if end["cut"] == "not_started":
                assert path not in started, (run.name, path)  # a never-started vertex did nothing
        assert not fold.fold_lane(run.run_dir, fold.plan_of_spec(_spec(run.run_dir))).unknown_paths
    # the real runs began: at least one leaf started, and the failure cut what depends on it
    by_name = {run.name: run for run in real_runs}
    cut = {p for p, e in ends_of(by_name["failure_dependents"].run_dir).items() if e["cut"]}
    assert cut, "failure_dependents: no dependent was cut"
    assert cut.isdisjoint(started_paths(by_name["failure_dependents"].run_dir))


@proves_started
@pytest.mark.parametrize(
    "foreign",
    [
        pytest.param(("data", "ghost"), id="under-a-composite"),
        pytest.param(("data", "db", "extra"), id="under-a-leaf"),
        pytest.param(("ghost",), id="top-level"),
        pytest.param(("Data",), id="case-variant"),
    ],
)
def test_planted_non_vertex_start_is_detected(
    tree_kernel: Kernel, foreign: tuple[str, ...]
) -> None:
    """A start at a path that is no vertex, appended to the lane in its own framing (the lane
    itself refuses to write one), is found by the fact and named by the fold, and the run's
    cleanup is then never read as clean (B4-I7)."""
    run = runs.admit(tree_kernel, generators.fixture_tree("three_level"))
    lane = runs.lane_of(run)
    runs.write_all_ends(run, lane)
    assert non_vertices(run.run_dir) == set()  # honest until the plant
    entries = len(lf.read_lane(lf.lane_path(run.run_dir)).entries)
    ghost = sup.step_entry(*foreign, kind=lf.StepKind.NO_ACTION, code="unit_no_action")
    append_ndjson(lf.lane_path(run.run_dir), lf.encode_record(ghost, seq=entries + 1))
    where = "/".join(foreign)
    assert non_vertices(run.run_dir) == {where}
    folded = fold.fold_lane(run.run_dir, run.plan)
    assert folded.unknown_paths == (where,)
    assert fold.cleanup_is_unknown(folded)
    assert where not in vertices_of(run.run_dir)


# ---- a child's view equals the root's account of it


@proves_view
@pytest.mark.parametrize("how", ["finalize", "recover"])
def test_child_view_equals_root_account(
    real_runs: list[Finished], tree_kernel: Kernel, how: str
) -> None:
    """For every non-root vertex of a finished run (real ones, and a planted one finalized or
    recovered), the child view's answer is the root answer's account of that vertex, byte for byte
    on the wire (B4-C8), the root's own view lists the same account for each vertex it lists, and
    the view carries the root's terminal state and this vertex's path."""
    finished = [planted_run(tree_kernel, how)]
    if how == "finalize":
        finished = [*real_runs, *finished]
    for run in finished:
        terminal = run.terminal
        rooted = answer.answer_for_run(
            run.run_dir,
            RunLedger.open(ledger_path(run.run_dir)).records,
            terminal,
            _spec(run.run_dir),
        )
        root_view = run.kernel.control.project.status(run.run_id)
        assert isinstance(root_view, RunView) and root_view.answer is not None
        inline = {
            tuple(node["path"]): node
            for node in [root_view.answer["primary"], *root_view.answer["listed"]]
        }
        paths = _paths(run)
        assert len(paths) >= 3, run.name
        for path in paths:
            view = _view(run, path)
            account = answer.account_of(rooted, path)
            assert account is not None, (run.name, path)
            assert view.answer == answer.node_wire(account), (run.name, path)
            assert (view.root_run_id, view.path, view.state) == (
                run.run_id,
                "/".join(path),
                terminal,
            ), (run.name, path)
            if path in inline:  # what the root's own answer lists is what the child says
                assert view.answer == inline[path], (run.name, path)


# ---- nothing under a finished run changes


@proves_frozen
def test_child_slice_and_evidence_unchanged_after_finalize(
    real_runs: list[Finished], tree_kernel: Kernel
) -> None:
    """The bytes of each vertex's slice of the lane, of every evidence file and of the result
    files, hashed at finalization, equal the bytes after every child view and the root's view are
    read and the observation window has passed; a restart's recovery over the finished run touches
    `meta.json` alone."""
    finished = [*real_runs, planted_run(tree_kernel), planted_run(tree_kernel, "recover")]
    for run in finished:
        at_finalization = freeze(run.run_dir)
        assert set(at_finalization.slices) >= {""} | vertices_of(run.run_dir) - {""}, run.name
        assert at_finalization.results, run.name  # there is a result to keep
        assert "child_views.json" in at_finalization.results, run.name
        for path in _paths(run):  # every view, twice: a read changes nothing
            _view(run, path)
            _view(run, path)
        root = run.kernel.control.project.status(run.run_id)
        assert isinstance(root, RunView)
        for sample in observe_window(run.run_dir):
            assert sample == at_finalization, run.name
        # a restart over the finished run re-materializes `meta.json` (its `recovered` marker,
        # one-vertex behaviour, unchanged) and nothing else: no slice, no result file, no other
        # evidence file
        recover_run_dir(run.run_dir)
        after = freeze(run.run_dir)
        assert after.slices == at_finalization.slices, run.name
        assert after.results == at_finalization.results, run.name
        changed = {
            name
            for name in at_finalization.evidence.keys() | after.evidence.keys()
            if at_finalization.evidence.get(name) != after.evidence.get(name)
        }
        assert changed <= {"meta.json"}, (run.name, changed)
    # a view that is created after the fact changes nothing either: the file is what was written
    first = real_runs[0]
    views_file = evidence_dir(first.run_dir) / answer.CHILD_VIEWS_FILE
    assert views_file.is_file()
    views = json.loads(views_file.read_text(encoding="utf-8"))
    assert views  # materialized at finalization, not lazily


def test_a_planted_write_to_a_slice_or_a_result_is_seen_by_freeze(tree_kernel: Kernel) -> None:
    """The negative control of the two tests above: a byte appended to a vertex's lane slice, to
    an evidence file, or a rewritten result file changes what `freeze` reports."""
    run = planted_run(tree_kernel)
    base = freeze(run.run_dir)
    evidence = evidence_dir(run.run_dir)
    (evidence / "late.log").write_text("1\n", encoding="utf-8")
    assert freeze(run.run_dir).evidence != base.evidence
    (evidence / "result.json").write_text('{"changed": true}', encoding="utf-8")
    assert freeze(run.run_dir).results != base.results
    ghost = sup.step_entry(*DB, kind=lf.StepKind.NO_ACTION, code="unit_no_action")
    count = len(lf.read_lane(lf.lane_path(run.run_dir)).entries)
    append_ndjson(lf.lane_path(run.run_dir), lf.encode_record(ghost, seq=count + 1))
    after = freeze(run.run_dir)
    assert after.slices["data/db"] != base.slices["data/db"]
    assert after.slices["data/cache"] == base.slices["data/cache"]  # only the slice written to


# ---- a survivor that writes into the child's slice, evidence and result (PROC, BOTH)

SHORT = [
    ("LEAF_BUDGET_S = 10", "LEAF_BUDGET_S = 3"),
    ("ROOT_BUDGET_S = 20", "ROOT_BUDGET_S = 5"),
    ("DEADLINE_S = 36", "DEADLINE_S = 7"),
    ("deadline=36", "deadline=7"),
    ("WAIT_MAX_S = 6", "WAIT_MAX_S = 1"),
]
SHORT_DEADLINE_S = 7
SHORT_RELEASE_S = 1.0


def _survivor_plugin(tmp_path: Path) -> Path:
    """`survivor_writer` with a run's clocks of a few seconds (the root deadline is 7 s)."""
    source = tk.fixture_source("survivor_writer")
    for old, new in SHORT:
        assert source.count(old) == 1, f"survivor_writer: {old!r} moved"
        source = source.replace(old, new)
    plugins = tmp_path / "plugins"
    plugins.mkdir(exist_ok=True)
    path = plugins / "survivor_writer.py"
    path.write_text(source, encoding="utf-8")
    return path


def _run_survivor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, stopper: Any = None
) -> tuple[harness.AdmittedTree, RunView | None, str]:
    """Run `survivor_writer` to its terminal row: the writers come up (two trees, an appender
    below each), the root deadline stops the run. Returns the run, its view and the argv tag."""
    monkeypatch.setattr(clock, "grace", support.TEST_GRACE_S)
    monkeypatch.setattr(clock, "kill", support.TEST_KILL_S)
    monkeypatch.setattr(clock, "release_slice", SHORT_RELEASE_S)
    monkeypatch.setattr(clock, "FINALIZATION_RESERVE_S", 1.0)
    monkeypatch.setenv("TRESTLE_FINALIZATION_RESERVE_S", "1")
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    path = _survivor_plugin(tmp_path)
    kernel = harness.fresh_kernel([path.parent], home=tmp_path / "home")
    monkeypatch.setenv("TRESTLE_HOME", str(tmp_path / "home"))
    if stopper is not None:
        kernel.control.conductor.stopper = stopper
    tag = str(tmp_path / "survivor-writer")
    assert len(tag) >= support.MIN_MARKER
    admitted = harness.admit_tree(path, {"tag": tag}, kernel=kernel)
    views: list[RunView] = []

    def drive() -> None:
        if stopper is None:
            views.append(harness.drive_tree(admitted))
        else:  # the survivors rewrite result.json under the host's read: the conductor alone
            kernel.control.conductor.drive(admitted.order)

    conductor = threading.Thread(target=drive)
    try:
        conductor.start()
        evidence = evidence_dir(admitted.run_dir)
        assert support.wait_until(
            lambda: len(list(evidence.glob("late_*.log"))) == 2, tolerances.JOIN_WAIT_S
        ), "the writers never wrote"
        assert len(support.marked(tag)) >= 4  # two middle processes, an appender under each
        conductor.join(timeout=SHORT_DEADLINE_S + clock.finalization_margin + clock.stop_bound)
        assert not conductor.is_alive(), "the run never reached its terminal row"
    except BaseException:
        ancestry.reap(support.marked(tag))
        raise
    return admitted, views[0] if views else None, tag


@pytest.mark.parametrize("stop", [pytest.param("root_deadline", id="proc")])
@proves_survivor[0]
@proves_survivor[1]
def test_survivor_cannot_write_child_slice_after_finalize(
    stop: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`survivor_writer`: `writer`'s descendants ignore SIGTERM and keep appending to the run's
    evidence and rewriting its result. The root deadline stops the run (`timed_out`, the writer
    cut `stopped`), and the bytes hashed at the terminal row (the writer's slice, every evidence
    file, every result file) equal those sampled across the observation window after it; no
    process carrying the run's tag or id is left (MC-13)."""
    assert stop == "root_deadline"
    admitted, view, tag = _run_survivor(tmp_path, monkeypatch)
    assert view is not None
    with support.reaping(tag), support.reaping(admitted.run_id):
        _assert_frozen_after_terminal(admitted, view, tag)


def _assert_frozen_after_terminal(admitted: harness.AdmittedTree, view: RunView, tag: str) -> None:
    run_dir = admitted.run_dir
    assert view.answer is not None
    assert view.answer["outcome"] == "timed_out"
    assert view.answer["root_stop"] == "release_point"
    # the writer is stopped, never ended on its own: its account is a stopped node's, and its
    # `NodeEnd`, when the child wrote one, is cut. The root declares no release walk, so its release
    # slice is 0 (B2-C1) and the kill follows the stop row at once (B2-C10): whether the child wrote
    # the end first is a race with the kill (a loaded runner loses it), not part of this proof.
    accounts = [view.answer["primary"], *view.answer["listed"]]
    writer = [n for n in accounts if n["path"] == ["writer"]]
    assert [n["listing"] for n in writer] in (["stopped"], ["unended"], ["not_started"]), writer
    writer_end = ends_of(run_dir).get("writer")
    assert writer_end is None or writer_end["cut"] in ("stopped", "not_started"), writer_end
    kinds = records.node_record(run_dir).kinds
    assert kinds[-1] == "timed_out"
    assert kinds.index("group_stop") < kinds.index("evidence_finalized")  # writers stopped first
    logs = sorted(evidence_dir(run_dir).glob("late_*.log"))
    assert len(logs) == 2 and all(log.read_text(encoding="utf-8") for log in logs)  # they wrote
    at_terminal = freeze(run_dir)
    assert "writer" in at_terminal.slices and at_terminal.results
    for sample in observe_window(run_dir):
        assert sample == at_terminal, "bytes under the run changed after the terminal row"
    assert not support.marked(tag), "a writer of the child is still alive"
    assert not support.marked(admitted.run_id)


def test_a_survivor_that_writes_after_the_terminal_row_is_seen_by_this_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The negative control: a stopper that reports the tree gone and stops nothing leaves the
    writers running, and the same window shows the evidence and result bytes changing after the
    terminal row (the check is not vacuous)."""
    admitted, _, tag = _run_survivor(
        tmp_path, monkeypatch, stopper=lambda attribution: GroupStop(True, False)
    )
    try:
        assert records.node_record(admitted.run_dir).kinds[-1] == "timed_out"
        at_terminal = freeze(admitted.run_dir)
        seen = observe_window(admitted.run_dir)
        assert any(sample != at_terminal for sample in seen)
        assert any(sample.results != at_terminal.results for sample in seen)
        assert support.marked(tag)  # the survivors are there to be seen (MC-13)
    finally:
        ancestry.reap(support.marked(tag))
