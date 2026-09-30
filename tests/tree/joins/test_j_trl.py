"""J-TRL (MJ.TR-L), authored by L.TR-L.1: the lift of the multi-vertex refusal.

T3 `test_refusal_narrowed_to_choice`: an `AllDeclaration` tree is admitted through the two host
entry points and reaches a terminal state, and the `ChoiceNode` case follows the phase
`meta register --probe multi-vertex-refusal` prints (`choice-only`: refused with the temporary code
and no run dir; `absent`, from L.TR-5.3: admitted), so the test stays true after L.TR-5.3 with no
edit. T1's file half: `lift_set_trl.toml` equals the frozen literal below, and its checker rejects
a ledger that lacks or fails a listed host node. T2's check function (`host_path_violations`) and
its planted self-test; L.TR-L.11 added the live node `test_lift_tests_are_host_path` over the
committed TREE-L modules, T6 (`test_trl_rollback_class_consistent`, over `d2_outcomes.toml` and
`rollback.toml`) and the drain-check case over the tree-trl fossils."""

from __future__ import annotations

import ast
import fnmatch
import json
import os
import shutil
import subprocess
import sys
import tomllib
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import pytest

from tests.proof import mcp_host, register, tolerances
from tests.tree import hostpath
from tests.tree.joins import lift_set_check
from tests.tree.test_hostpath import harness_imports
from tests.tree.test_tr1_admission import run_dirs, source
from trestle.common import codes
from trestle.common.types import PublishView, RequestOutcome, RunView
from trestle.server.main import Kernel

REPO = Path(__file__).resolve().parents[3]
TREES = REPO / "tests" / "fixtures" / "trees"
FOSSILS = REPO / "tests" / "fixtures" / "fossils" / "tree-trl"
D2_OUTCOMES = Path(__file__).with_name("d2_outcomes.toml")
ROLLBACK = REPO / "tests" / "proof" / "rollback.toml"
TERMINAL_STATES = ("succeeded", "failed", "cancelled", "timed_out")
HOST_TIMEOUT_S = tolerances.JOIN_WAIT_S * 6

# MC-B3-04, frozen: the clauses TM-B2-1's `full` phase held, and the host nodes that prove each
# (`tests/tree/joins/lift_set_trl.toml` must equal this literal).
FROZEN_LIFT_SET: dict[str, list[str]] = {
    "A1.5": [
        "tests/tree/host/test_trl_rollup.py::test_permutation_and_depth_same_class_primary",
        "tests/tree/host/test_trl_rollup.py::test_two_trigger_race_lists_reached_conditions",
        "tests/tree/host/test_trl_rollup.py::test_depth2_conditions_one_call",
        "tests/tree/host/test_trl_rollup.py::test_root_stops_depth2_active",
        "tests/tree/host/test_trl_rollup.py::test_root_stop_after_ordinary_failure[cancel]",
        "tests/tree/host/test_trl_rollup.py::test_root_stop_after_ordinary_failure[deadline]",
        "tests/tree/host/test_trl_rollup.py::test_child_view_class_equals_root_account",
    ],
    "A2.6": [
        "tests/tree/host/test_trl_lineage.py::test_forge_request_fields_not_honoured",
        "tests/tree/host/test_trl_lineage.py::test_unit_code_cannot_change_recorded_path",
        "tests/tree/host/test_trl_lineage.py::test_every_child_view_resolves_names_root_path",
        "tests/tree/host/test_trl_lineage.py::test_shared_node_one_start_one_disposition",
        "tests/tree/host/test_trl_lineage.py::test_started_paths_within_declaration",
    ],
    "A4.2": [
        "tests/tree/host/test_trl_barrier.py::test_host_barrier_order_and_bound",
        "tests/tree/host/test_trl_barrier.py::test_host_upstream_covered_precondition_runs",
    ],
    "A5.4": [
        "tests/tree/host/test_trl_slices.py::"
        "test_noncooperating_child_dead_by_root_deadline_margin_kill",
        "tests/tree/host/test_trl_slices.py::test_cooperating_child_timed_out_names_path",
    ],
    "A6.3": [
        "tests/tree/host/test_trl_cancel.py::test_root_cancel_readiness_wait_and_running_sibling",
        "tests/tree/host/test_trl_cancel.py::test_root_deadline_same_clauses",
        "tests/tree/host/test_trl_failure.py::test_exception_stops_siblings_listed_in_answer",
        "tests/tree/host/test_trl_failure.py::test_restricted_owner_reads_child_view",
        "tests/tree/host/test_trl_failure.py::test_child_addressed_cancel",
    ],
    "A6.4": [
        "tests/tree/host/test_trl_failure.py::test_ordinary_failure_stops_only_dependents",
    ],
    "A8.4": [
        "tests/tree/host/test_trl_ownership.py::test_unit5_host_variant[sibling_fail]",
        "tests/tree/host/test_trl_ownership.py::test_unit5_host_variant[exception]",
        "tests/tree/host/test_trl_ownership.py::test_unit5_host_variant[cancel]",
        "tests/tree/host/test_trl_ownership.py::test_unit5_host_variant[deadline]",
        "tests/tree/host/test_trl_ownership.py::test_unit5_host_variant[restart]",
        "tests/tree/host/test_trl_ownership.py::test_host_claim_before_effect",
        "tests/tree/host/test_trl_ownership.py::test_host_no_release_before_root_phase",
    ],
    "A8.5": [
        "tests/tree/host/test_trl_lease.py::test_root_and_direct_child_never_overlap",
        "tests/tree/host/test_trl_lease.py::test_child_never_queued_behind_root",
        "tests/tree/host/test_trl_lease.py::test_direct_call_acquires_before_effect",
    ],
}
HOSTPATH_MODULE = "tests.tree.hostpath"


def probe_phase(capsys: pytest.CaptureFixture[str]) -> str:
    """`meta register --probe multi-vertex-refusal`: `full`, `choice-only` or `absent`."""
    assert register.cmd_probe("multi-vertex-refusal") in (0, 1)
    return capsys.readouterr().out.strip()


@pytest.fixture
def mcp(tmp_path: Path) -> Any:
    with mcp_host.McpHost(home=tmp_path / "host-home", timeout_s=HOST_TIMEOUT_S) as host:
        yield host


def _plant(host: mcp_host.McpHost, name: str) -> None:
    shutil.copy(TREES / f"{name}.py", host.home / "plugins")


def _no_run_dirs(home: Path) -> None:
    runs = home / "runs"
    assert not runs.exists() or not [p for p in runs.glob("*/*") if p.is_dir()], "a run dir exists"


def test_refusal_narrowed_to_choice(
    tree_kernel: Kernel, mcp: mcp_host.McpHost, capsys: pytest.CaptureFixture[str]
) -> None:
    """T3: `two_branch_barrier` (MC-B3-01) is admitted through `ControlSurface.run` and the MCP
    `run` tool, a run id is minted and the run is terminal; the `ChoiceNode` fixture follows the
    probe's phase."""
    phase = probe_phase(capsys)
    assert phase in ("choice-only", "absent"), f"the AllDeclaration refusal is still up: {phase!r}"

    barrier = source("two_branch_barrier")
    published = hostpath.publish_tree_via_host(tree_kernel, barrier)
    assert isinstance(published, PublishView), published
    view = hostpath.run_tree_via_host(tree_kernel, published.name)
    assert isinstance(view, RunView), view
    assert view.state in TERMINAL_STATES, view.state
    assert [d.name for d in run_dirs(tree_kernel)] == [view.run_id]

    _plant(mcp, "two_branch_barrier")
    wired = hostpath.mcp_run_tree(mcp, "two_branch_barrier")
    assert "code" not in wired and str(wired["run_id"]).startswith("r_"), wired
    assert wired["state"] in TERMINAL_STATES, wired

    # the ChoiceNode case follows the phase: refused with the temporary code and no run dir while
    # `choice-only`; admitted once the refusal is gone (`absent`)
    choice = source("choice_fake")
    choice_published = hostpath.publish_tree_via_host(tree_kernel, choice)
    assert isinstance(choice_published, PublishView), choice_published
    dirs_before = run_dirs(tree_kernel)
    outcome = hostpath.run_tree_via_host(tree_kernel, choice_published.name)
    if phase == "choice-only":
        assert isinstance(outcome, RequestOutcome), outcome
        assert outcome.code == codes.ADMISSION_PLAN_MULTI_VERTEX_UNSUPPORTED
        assert run_dirs(tree_kernel) == dirs_before
    else:
        assert isinstance(outcome, RunView), outcome


def test_admitted_all_tree_runs_through_the_walk(mcp: mcp_host.McpHost) -> None:
    """The lift is real: an `AllDeclaration` tree whose plugin runs its tree (`exception_branch`,
    L.TR-3.6) is admitted by the host and driven by the tree walk to a terminal answer that names
    the raising node."""
    _plant(mcp, "exception_branch")
    answer = mcp.call(
        "run",
        {
            "plugin": "exception_branch",
            "args": {"env": "dev"},
            "wait_ms": hostpath.WAIT_MS,
            "completion": "terminal",
        },
    )
    assert "code" not in answer and str(answer["run_id"]).startswith("r_"), answer
    assert answer["state"] in TERMINAL_STATES, answer
    primary = answer["answer"]["primary"]
    assert primary["path"] == ["raiser"] and primary["code"] == codes.UNIT_RAISED, primary


def test_lift_set_file_matches_frozen_set() -> None:
    """T1's file half (MC-B3-04): `lift_set_trl.toml` is the frozen literal, over exactly the eight
    clauses of TM-B2-1's `full` phase (A1.5, A2.6, A4.2, A5.4, A6.3, A6.4, A8.4, A8.5)."""
    on_disk = lift_set_check.load_lift_set()
    assert on_disk == FROZEN_LIFT_SET
    assert sorted(on_disk) == ["A1.5", "A2.6", "A4.2", "A5.4", "A6.3", "A6.4", "A8.4", "A8.5"]
    assert all(nodes for nodes in on_disk.values())
    assert all(node.startswith("tests/tree/host/test_trl_") for n in on_disk.values() for node in n)


def _record(node: str, clause: str, **override: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "nodeid": node,
        "outcome": "passed",
        "gate": "ci-tests",
        "venue": "CI",
        "interpreter": "3.12.8",
        "labels": [clause],
    }
    record.update(override)
    return record


def _synthetic_ledger(**plant: Any) -> list[dict[str, Any]]:
    """A complete ledger of the frozen lift set; `plant` maps a node id to `None` (drop it) or to
    record overrides (change it)."""
    records = []
    for clause, nodes in FROZEN_LIFT_SET.items():
        for node in nodes:
            override = plant.get(node, {})
            if override is None:
                continue
            records.append(_record(node, clause, **override))
    return records


def _write(path: Path, records: list[dict[str, Any]]) -> Path:
    path.write_text(json.dumps(records), encoding="utf-8")
    return path


def test_lift_set_closed_rejects_planted_missing_node(tmp_path: Path) -> None:
    """T1's checker fails a ledger that lacks one listed host node, one where that node is
    xfailed (or skipped, or failed), one whose record does not carry the clause and one that
    counted only on 3.14 or at a LOCAL venue; a complete ledger passes."""
    victim = FROZEN_LIFT_SET["A8.4"][0]
    complete = _write(tmp_path / "complete.json", _synthetic_ledger())
    assert lift_set_check.check(complete) == []

    missing = _write(tmp_path / "missing.json", _synthetic_ledger(**{victim: None}))
    (problem,) = lift_set_check.check(missing)
    assert victim in problem and "no record" in problem

    for outcome in ("xfailed", "skipped", "failed", "xpassed"):
        planted = _write(
            tmp_path / f"{outcome}.json", _synthetic_ledger(**{victim: {"outcome": outcome}})
        )
        (problem,) = lift_set_check.check(planted)
        assert victim in problem and outcome in problem

    for override in ({"labels": ["A8.5"]}, {"interpreter": "3.14.0"}, {"venue": "LOCAL"}):
        planted = _write(tmp_path / "other.json", _synthetic_ledger(**{victim: override}))
        (problem,) = lift_set_check.check(planted)
        assert victim in problem and "no passing 3.12" in problem

    # a clause-keyed report (`meta report --json`) names no node: refused, not vacuously passed
    report = tmp_path / "report.json"
    report.write_text('{"A8.4": {"status": "PROVEN"}}', encoding="utf-8")
    (problem,) = lift_set_check.check(report)
    assert "cannot read the ledger" in problem

    # the closed-set test itself fails, never skips, when no ledger is named
    with pytest.raises(AssertionError, match="is unset"):
        saved = os.environ.pop(lift_set_check.LEDGER_ENV, None)
        try:
            lift_set_check.test_lift_set_closed()
        finally:
            if saved is not None:
                os.environ[lift_set_check.LEDGER_ENV] = saved


def host_path_violations(modules: Iterable[Path]) -> list[str]:
    """T2's check (MC-B3-02): each lift-set module exists (a missing one is a violation, never a
    skip), imports `tests/tree/hostpath.py`, and imports nothing from `tests/proof/harness.py`."""
    problems: list[str] = []
    for path in modules:
        if not path.is_file():
            problems.append(f"{path}: the module does not exist")
            continue
        text = path.read_text(encoding="utf-8")
        for reached in harness_imports(text):
            problems.append(f"{path}: imports the MC-26 harness ({reached})")
        tree = ast.parse(text)
        reaches_hostpath = any(
            (isinstance(n, ast.ImportFrom) and (n.module or "").startswith(HOSTPATH_MODULE))
            or (
                isinstance(n, ast.ImportFrom)
                and n.module == "tests.tree"
                and any(a.name == "hostpath" for a in n.names)
            )
            or (
                isinstance(n, ast.Import)
                and any(a.name.startswith(HOSTPATH_MODULE) for a in n.names)
            )
            for n in ast.walk(tree)
        )
        if not reaches_hostpath:
            problems.append(f"{path}: does not import tests/tree/hostpath.py")
    return problems


def lift_set_modules() -> list[Path]:
    """The TREE-L host modules named by the lift set, from its node ids."""
    names = {node.split("::", 1)[0] for nodes in FROZEN_LIFT_SET.values() for node in nodes}
    return [REPO / name for name in sorted(names)]


def test_lift_tests_are_host_path_rejects_planted_harness_import(tmp_path: Path) -> None:
    """T2's check function fails on a planted module importing `tests/proof/harness.py` (each way
    of reaching it), on one that never imports the host-path runner and on a missing module; a
    module through `tests/tree/hostpath.py` alone passes."""
    good = tmp_path / "test_trl_good.py"
    good.write_text("from tests.tree import hostpath\n\n\ndef test_x():\n    hostpath.WAIT_MS\n")
    assert host_path_violations([good]) == []
    also_good = tmp_path / "test_trl_good2.py"
    also_good.write_text("from tests.tree.hostpath import run_tree_via_host\n")
    assert host_path_violations([also_good]) == []

    for planted_text in (
        "from tests.tree import hostpath\nfrom tests.proof import harness\n",
        "from tests.tree import hostpath\nfrom tests.proof.harness import drive_tree\n",
        "from tests.tree import hostpath\nimport tests.proof.harness\n",
        "from tests.tree import hostpath\nfrom tests.proof.harness import run_tree as go\n",
    ):
        planted = tmp_path / "test_trl_planted.py"
        planted.write_text(planted_text)
        problems = host_path_violations([planted])
        assert problems and all("imports the MC-26 harness" in p for p in problems), problems

    bare = tmp_path / "test_trl_bare.py"
    bare.write_text("import pytest\n")
    (problem,) = host_path_violations([bare])
    assert "does not import tests/tree/hostpath.py" in problem
    (problem,) = host_path_violations([tmp_path / "test_trl_absent.py"])
    assert "does not exist" in problem


def test_lift_tests_are_host_path() -> None:
    """T2's live node (L.TR-L.11): every module `lift_set_trl.toml` lists exists, imports
    `tests/tree/hostpath.py` and imports nothing from `tests/proof/harness.py`. A listed module that
    is missing fails it, never skips it."""
    modules = lift_set_modules()
    assert modules, "the lift set lists no module"
    assert host_path_violations(modules) == []


def test_lift_tests_are_host_path_fails_when_a_listed_module_is_removed(tmp_path: Path) -> None:
    """The live check over a temporary copy of the committed modules passes; removing one listed
    module from the copy fails it (never skips), naming the module."""
    copies = []
    for module in lift_set_modules():
        target = tmp_path / module.name
        shutil.copy(module, target)
        copies.append(target)
    assert host_path_violations(copies) == []
    copies[0].unlink()
    (problem,) = host_path_violations(copies)
    assert copies[0].name in problem and "does not exist" in problem


# T5/T6 (MC-31, DM-79): the D2 outcome record and the class TR-L's boundary carries.
INFLIGHT_STATES = "*/trl-inflight-*"
EVIDENCE = "tests/tree/joins/test_j_trl.py::test_trl_rollback_class_consistent"


def trl_class_for(runs: list[dict[str, Any]]) -> str | None:
    """The class the recorded TR-L D2 runs imply (DM-79, A2c3-10): `transparent` iff every exit is
    0; `drain` iff some exit is non-zero, some unexcused state diverged and every diverged path is
    an in-flight state; `None` otherwise (a terminal or views state diverged, or a run failed
    without naming a diverging state: no class is written, the band escalates to the root)."""
    tr_l = [run for run in runs if run.get("merge") == "TR-L"]
    if not tr_l:
        return None
    if all(run["exit"] == 0 for run in tr_l):
        return "transparent"
    diverged = [path for run in tr_l for path in run["diverged"]]
    if diverged and all(fnmatch.fnmatch(path, INFLIGHT_STATES) for path in diverged):
        return "drain"
    return None


def trl_class_problems(runs: list[dict[str, Any]], boundaries: list[dict[str, Any]]) -> list[str]:
    """T6's check over the two records: TR-L's boundary carries the class the D2 runs imply and
    names this test as its evidence."""
    expected = trl_class_for(runs)
    if expected is None:
        return [
            "no class follows from the recorded TR-L D2 runs (a terminal or views state diverged, "
            f"or no TR-L run is recorded): {runs}"
        ]
    entries = [b for b in boundaries if b["merge"] == "TR-L"]
    if len(entries) != 1:
        return [f"TR-L is registered {len(entries)} times in rollback.toml"]
    problems = []
    if entries[0]["class"] != expected:
        problems.append(f"TR-L is {entries[0]['class']!r}, the D2 outcomes imply {expected!r}")
    if entries[0]["evidence"] != EVIDENCE:
        problems.append(f"TR-L's evidence is {entries[0]['evidence']!r}, not {EVIDENCE!r}")
    return problems


@pytest.mark.proves(
    "C-CONTAIN-AND-CLOCK",
    "C-CONTAIN-AND-CLOCK:trl-rollback-class-recorded",
    "A",
    "tree",
    "LOGIC",
    "CI",
)
def test_trl_rollback_class_consistent() -> None:
    """T6: TR-L's MC-31 class is consistent with its recorded D2 outcome, whichever class it is:
    `transparent` iff every recorded TR-L exit is 0, `drain` iff some exit is non-zero and every
    diverged path is an in-flight state. Reads only `d2_outcomes.toml` and `rollback.toml`: never
    re-runs d2 and never reads the live `d2_exceptions.toml` (DM-79)."""
    runs = tomllib.loads(D2_OUTCOMES.read_text(encoding="utf-8")).get("run", [])
    boundaries = tomllib.loads(ROLLBACK.read_text(encoding="utf-8")).get("boundary", [])
    assert trl_class_problems(runs, boundaries) == []


def _run(exit_code: int, *diverged: str) -> dict[str, Any]:
    return {
        "merge": "TR-L",
        "reader": "wr-ckpt/single",
        "reader_sha": "0" * 40,
        "fossils": "tests/fixtures/fossils/tree-trl",
        "exit": exit_code,
        "diverged": list(diverged),
    }


def test_rollback_class_rule_rejects_planted_inconsistencies() -> None:
    """The class rule follows exit codes and diverged paths: a planted `transparent` beside a
    non-zero exit and a planted `drain` beside a diverged `trl-terminal-*` (or views) path are both
    inconsistent with what the rule derives."""
    assert trl_class_for([_run(0), _run(0)]) == "transparent"
    inflight = "tree-trl/trl-inflight-readiness_sibling"
    assert trl_class_for([_run(0), _run(1, inflight)]) == "drain"
    # a planted `transparent` beside a non-zero exit fails T6's check; the matching `drain` passes
    planted = [{"merge": "TR-L", "class": "transparent", "evidence": EVIDENCE}]
    (problem,) = trl_class_problems([_run(0), _run(1, inflight)], planted)
    assert "'transparent'" in problem and "'drain'" in problem
    assert (
        trl_class_problems([_run(0), _run(1, inflight)], [{**planted[0], "class": "drain"}]) == []
    )
    # a planted `drain` beside a diverged terminal or views path: no class follows at all
    terminal = "tree-trl/trl-terminal-exception_branch"
    views = "tree-trl/trl-views-upstream_covered"
    assert trl_class_for([_run(1, inflight, terminal)]) is None
    drain = [{"merge": "TR-L", "class": "drain", "evidence": EVIDENCE}]
    (problem,) = trl_class_problems([_run(1, terminal)], drain)
    assert "no class follows" in problem
    assert trl_class_for([_run(1, views)]) is None
    # a run that failed without naming a diverging state is not a drain
    assert trl_class_for([_run(1)]) is None
    assert trl_class_for([]) is None


@pytest.mark.parametrize("band", ["trl"])
def test_drain_check_counts_nonterminal_tree_root(band: str, tmp_path: Path) -> None:
    """SV-3's drain procedure covers a TR-L root (A2c3-8): `meta drain-check` over a copy of a
    `trl-inflight-*` fossil home exits 1 and names the root; over a `trl-terminal-*` home it
    exits 0."""

    def drain_check(state: Path) -> subprocess.CompletedProcess[str]:
        home = tmp_path / f"home-{state.name}"
        shutil.copytree(state / "home", home)
        return subprocess.run(
            [sys.executable, "-m", "tests.proof.meta", "drain-check", "--home", str(home)],
            cwd=REPO,
            env={"PYTHONPATH": str(REPO), "PATH": "/usr/bin:/bin"},
            capture_output=True,
            text=True,
            check=False,
        )

    inflight = sorted(FOSSILS.glob(f"{band}-inflight-*"))
    terminal = sorted(FOSSILS.glob(f"{band}-terminal-*"))
    assert inflight and terminal, "the tree-trl fossils are not committed"
    for state in inflight:
        blocked = drain_check(state)
        assert blocked.returncode == 1, blocked.stdout
        assert "1 non-terminal plan-bearing root" in blocked.stdout, blocked.stdout
    for state in terminal:
        drained = drain_check(state)
        assert drained.returncode == 0, drained.stdout
        assert "0 non-terminal plan-bearing root" in drained.stdout, drained.stdout
