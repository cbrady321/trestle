"""L.TR-5.3: a ChoiceNode tree through the host (the last temporary refusal is gone).

TM-B2-1's remaining phase refused a `ChoiceNode` root before any run id. It is removed, so a
choice tree is admitted like any other and its selection (B1-O4) and live-state stops (V-7.3,
V-7.4, B1-E7, J-5a) can be seen from the caller's side, through the two entry points a caller has
(MC-12; `tests/tree/hostpath.py`, MC-B3-02; no `tests/proof/harness.py`, J-TRL T2's rule):

* `test_choice_tree_admitted_and_selects`: `choice_fake` (declaration only) is admitted with a run
  id and a plan of three vertices under a `ChoiceNode` root; `live_state` (`healthy`) runs its two
  choices to a `passed` answer, twice, with the same selection and the same observation digest,
  and an alternative the selection left out has no `NodeEnd`;
* `test_live_state_via_mcp_one_class_code`: each live-state case of `live_state` (an absent
  externally managed instance, a drifted definition, an infeasible route, a missing toolchain)
  gives one call, one class and a code naming the identifier, and the stopped node has no effect
  after the condition;
* `test_choice_plan_identity_before_first_effect_proc` (PROC): on a real `ControlSurface.run` the
  selection observations and the plan identity precede the first effect record anywhere in the
  root, and the identity is queryable afterwards (`run_events`)."""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.fixtures.trees import live_state
from tests.proof import mcp_host, records, tolerances
from tests.tree import hostpath
from trestle.common.types import PublishView, RunView
from trestle.server.main import Kernel

REPO = Path(__file__).resolve().parents[3]
TREES = REPO / "tests" / "fixtures" / "trees"
HOST_TIMEOUT_S = tolerances.JOIN_WAIT_S * 6
TERMINAL_STATES = ("succeeded", "failed", "cancelled", "timed_out")

proves_selection = pytest.mark.proves(
    "WR-UNIT-8", "WR-UNIT-8:selection-validated-only", "A", "tree", "MCP+LOGIC", "CI"
)
proves_live_state = pytest.mark.proves(
    "WR-UNIT-8", "WR-UNIT-8:live-state-stops", "A", "tree", "MCP+LOGIC", "CI"
)
proves_plan = pytest.mark.proves("WR-PLAN-3", "WR-PLAN-3:tree", "A", "tree", "PROC", "CI")

# the selection `healthy` makes: `managed_data` is up (an instance somebody else runs, reused);
# nothing of `edge` is up, so its declared fallback (`docker_edge`, the only container-reachable
# alternative) is chosen
HEALTHY_SELECTION = {"data": "data/managed_data", "edge": "edge/docker_edge"}


@pytest.fixture(scope="module")
def mcp(tmp_path_factory: pytest.TempPathFactory) -> Iterator[mcp_host.McpHost]:
    """One `trestle serve` for the module (each run has its own run id and directory)."""
    home = tmp_path_factory.mktemp("tr5-host") / "host-home"
    with mcp_host.McpHost(home=home, timeout_s=HOST_TIMEOUT_S) as host:
        for name in ("choice_fake", "live_state"):
            shutil.copy(TREES / f"{name}.py", host.home / "plugins")
        yield host


def _run(host: mcp_host.McpHost, plugin: str, case: str | None = None) -> dict[str, Any]:
    args: dict[str, Any] = {} if case is None else {"env": "dev", "case": case}
    wired = hostpath.mcp_run_tree(host, plugin, args)
    assert "code" not in wired and str(wired["run_id"]).startswith("r_"), wired
    assert wired["state"] in TERMINAL_STATES, wired
    return wired


def _run_dir(home: Path, run_id: str) -> Path:
    (found,) = sorted((home / "runs").glob(f"*/{run_id}"))
    return found


def _lane(run_dir: Path) -> list[Any]:
    lane = records.lane_rows(run_dir)
    assert not lane.problems and not lane.torn, lane.problems
    return lane.rows


def _plan(rows: list[Any]) -> dict[str, Any]:
    plans = [r.entry for r in rows if r.cls == "plan"]
    assert len(plans) == 1, "the plan identity is recorded exactly once"
    return plans[0]


def _ends(rows: list[Any]) -> dict[str, dict[str, Any]]:
    return {str(r.entry["path"]): r.entry for r in rows if r.cls == "end"}


def _issues(rows: list[Any], path: str) -> list[dict[str, Any]]:
    return [r.entry for r in rows if r.cls == "issue" and r.entry["path"] == path]


@proves_selection
def test_choice_tree_admitted_and_selects(mcp: mcp_host.McpHost) -> None:
    # `choice_fake`: a ChoiceNode root is admitted through the one call (no temporary code, a run
    # id, one run directory). It is declaration only, so its plugin never walks the tree.
    admitted = _run(mcp, "choice_fake")
    spec = json.loads(
        (_run_dir(mcp.home, admitted["run_id"]) / "evidence" / "spec.json").read_text("utf-8")
    )
    vertices = {v["path"]: v for v in spec["plan"]["vertices"]}
    assert vertices[""]["compose"] == "choice", vertices[""]
    assert len(vertices) == 3, sorted(vertices)

    # `live_state` (healthy): two choices run to a passed answer, twice, identically
    first = _run(mcp, "live_state", "healthy")
    second = _run(mcp, "live_state", "healthy")
    assert first["run_id"] != second["run_id"]
    for wired in (first, second):
        assert wired["answer"]["outcome"] == "passed", wired["answer"]
    live_spec = json.loads(
        (_run_dir(mcp.home, first["run_id"]) / "evidence" / "spec.json").read_text("utf-8")
    )
    declared = {v["path"] for v in live_spec["plan"]["vertices"]}
    rows = [_lane(_run_dir(mcp.home, w["run_id"])) for w in (first, second)]
    plans = [_plan(r) for r in rows]
    assert plans[0]["selection"] == plans[1]["selection"] == HEALTHY_SELECTION
    assert plans[0]["observations_digest"] == plans[1]["observations_digest"]
    assert plans[0]["observations_digest"], "the selection's observations are digested"
    assert plans[0]["args_hash"] == plans[1]["args_hash"]
    # every started path is a validated vertex; the alternatives the selection left out are not
    # walked (no `NodeEnd`, no ticket) and are `not_started` views
    for run_rows in rows:
        ends = _ends(run_rows)
        for left_out in ("data/local_data", "edge/managed_edge"):
            assert left_out not in ends and not _issues(run_rows, left_out), left_out
        assert {p for p in ends if p} <= declared, sorted(ends)
    # a different machine state selects differently: with nothing of `data` up, its fallback
    absent = _run(mcp, "live_state", "absent_em_instance")
    absent_plan = _plan(_lane(_run_dir(mcp.home, absent["run_id"])))
    assert absent_plan["selection"]["data"] == "data/managed_data"  # the declared fallback


@proves_live_state
@pytest.mark.parametrize(
    "case", ["absent_em_instance", "drifted_definition", "infeasible_route", "missing_toolchain"]
)
def test_live_state_via_mcp_one_class_code(mcp: mcp_host.McpHost, case: str) -> None:
    wired = _run(mcp, "live_state", case)
    answer = wired["answer"]
    rows = _lane(_run_dir(mcp.home, wired["run_id"]))
    primary = answer["primary"]
    if case == "absent_em_instance":
        assert answer["outcome"] == "blocked"
        assert primary["path"] == ["data", "managed_data"]
        assert primary["code"] == live_state.ABSENT_CODE
        assert primary["human_action"] == live_state.ABSENT_ACTION  # what to start, then re-send
        assert not _issues(rows, "data/managed_data") and not _issues(rows, "client")
        assert _ends(rows)["client"]["cut"] == "not_started"  # the dependent never started
    elif case == "drifted_definition":
        assert answer["outcome"] == "execution_error"
        assert primary["path"] == [] and primary["code"] == "execution.declaration_stale"
        assert [r for r in rows if r.cls == "issue"] == []  # not one ticket anywhere
    elif case == "infeasible_route":
        assert answer["outcome"] == "blocked"
        assert primary["path"] == ["gateway"] and primary["code"] == "admission.route_unsupported"
        assert "gateway" in primary["human_action"], primary  # names the identifier
        assert not _issues(rows, "gateway")
    else:
        assert answer["outcome"] == "blocked"
        assert primary["path"] == ["client"] and primary["code"] == "execution.toolchain_missing"
        assert live_state.TOOL in primary["human_action"], primary  # names the missing tool
        statuses = [r.entry["status"] for r in rows if r.cls == "confirmation"]
        assert statuses.count("not_applied") == 1  # the one refused create; nothing was started
    # one call, one class: the primary is the one node that carries the class (and the code); no
    # other node that reached a condition of its own is anything but `passed`
    assert primary["node_class"] == answer["outcome"], primary
    others = [
        item
        for item in answer["listed"]
        if item["listing"] == "candidate" and item["node_class"] not in ("passed", None)
    ]
    assert others == [], others


@proves_plan
def test_choice_plan_identity_before_first_effect_proc(tree_kernel: Kernel) -> None:
    published = hostpath.publish_tree_via_host(
        tree_kernel, (TREES / "live_state.py").read_text(encoding="utf-8")
    )
    assert isinstance(published, PublishView), published
    view = hostpath.run_tree_via_host(
        tree_kernel, published.name, {"env": "dev", "case": "healthy"}
    )
    assert isinstance(view, RunView), view
    assert view.state in TERMINAL_STATES, view.state
    run_dir = _run_dir(tree_kernel.home, view.run_id)
    rows = _lane(run_dir)
    classes = [r.cls for r in rows]
    # the lane: the plan entry first, exactly once, before the first effect record of any node
    assert classes[0] == "plan" and classes.count("plan") == 1, classes
    assert "issue" in classes and classes.index("plan") < classes.index("issue"), classes

    # the event stream: every selection observation, then the identity, then the first action
    events = _events(tree_kernel, view.run_id)
    kinds = [e["kind"] for e in events]
    assert kinds.count("plan.identity") == 1, kinds
    identity_at = kinds.index("plan.identity")
    selection_at = [
        i
        for i, e in enumerate(events)
        if e["kind"] == "step.observed" and e["payload"].get("phase") == "selection"
    ]
    assert len(selection_at) >= 2 and max(selection_at) < identity_at, kinds
    first_action = min(i for i, k in enumerate(kinds) if k == "step.action")
    assert identity_at < first_action, kinds

    # and it is queryable afterwards: the recorded selection, the observation digest, the plan's
    # digest (the spec's), all consistent with the lane entry
    payload = events[identity_at]["payload"]
    plan = _plan(rows)
    spec = json.loads((run_dir / "evidence" / "spec.json").read_text(encoding="utf-8"))
    assert payload["declaration_digest"] == plan["declaration_digest"]
    assert payload["declaration_digest"] == spec["plan"]["declaration_digest"]
    assert payload["plan_digest"], payload
    assert {item["choice"]: item["selected"] for item in payload["selection"]} == HEALTHY_SELECTION
    assert plan["selection"] == HEALTHY_SELECTION


def _events(kernel: Kernel, run_id: str) -> list[dict[str, Any]]:
    """Every `run_events` row of a run through the query view, following `next_cursor`."""
    out: list[dict[str, Any]] = []
    cursor: str | None = None
    for _ in range(50):
        page = kernel.control.query("run_events", {"run_id": run_id}, cursor)
        assert isinstance(page, dict) and "items" in page, page
        out.extend(page["items"])
        cursor = page.get("next_cursor")
        if not cursor:
            return out
    raise AssertionError("run_events did not end within 50 pages")
