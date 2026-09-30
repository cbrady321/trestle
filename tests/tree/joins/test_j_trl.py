"""J-TRL (MJ.TR-L), authored by L.TR-L.1: the lift of the multi-vertex refusal.

T3 `test_refusal_narrowed_to_choice`: an `AllDeclaration` tree is admitted through the two host
entry points and reaches a terminal state, and the `ChoiceNode` case follows the phase
`meta register --probe multi-vertex-refusal` prints (`choice-only`: refused with the temporary code
and no run dir; `absent`, from L.TR-5.3: admitted), so the test stays true after L.TR-5.3 with no
edit. T1's file half: `lift_set_trl.toml` equals the frozen literal below, and its checker rejects
a ledger that lacks or fails a listed host node. T2's check function (`host_path_violations`) and
its planted self-test; L.TR-L.11 adds the live node `test_lift_tests_are_host_path` over the
committed TREE-L modules, and T6, which read inputs that exist only after them."""

from __future__ import annotations

import ast
import json
import os
import shutil
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
