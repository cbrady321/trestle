"""L.TR-L.9: the host re-run of the WR-UNIT-6 failure and owner-view claims (J-TRL T1, T9).

Each case is one MCP call on a `trestle serve` subprocess (MC-12) over a behaviour fixture the
lifted refusal now admits: an exception in one node stops its siblings and is listed in the
answer (`exception_branch`); an ordinary failure cuts only its dependents (`failure_dependents`);
the restricted profile scopes a child view to the session that admitted its root (two MC-12
sessions on one `TRESTLE_HOME`); and a cancel addressed to a child handle gets the assumed
`projection.cancel_not_root` while the root run continues to its own terminal class (OQ-27, the
claim is gated). The module reaches admission only through the MCP tools (`tests/tree/hostpath.py`,
MC-B3-02) and imports nothing from `tests/proof/harness.py` (J-TRL T2)."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.proof import mcp_host, records, tolerances
from tests.tree import hostpath
from trestle.common import codes
from trestle.server import answer as answer_mod
from trestle.server.runs import cancel_flag_path

proves_exception = pytest.mark.proves(
    "WR-UNIT-6", "WR-UNIT-6:exception-siblings-stop", "A", "tree", "PROC+MCP", "BOTH"
)
proves_listed = pytest.mark.proves(
    "WR-UNIT-6", "WR-UNIT-6:exception-in-answer", "A", "tree", "PROC+MCP", "BOTH"
)
proves_dependents = pytest.mark.proves(
    "WR-UNIT-6", "WR-UNIT-6:ordinary-failure-dependents-only", "A", "tree", "PROC+MCP", "BOTH"
)
proves_owner = pytest.mark.proves(
    "WR-UNIT-6", "WR-UNIT-6:owner-reads-child-view", "A", "tree", "PROC+MCP", "BOTH"
)
proves_auth = pytest.mark.proves("WR-AUTH-1", "WR-AUTH-1:tree", "A", "tree", "MCP", "CI")
proves_a63 = pytest.mark.proves("WR-UNIT-6", "A6.3", "A", "tree", "PROC+MCP", "BOTH")
proves_a64 = pytest.mark.proves("WR-UNIT-6", "A6.4", "A", "tree", "PROC+MCP", "BOTH")
proves_cancel = pytest.mark.proves(
    "WR-UNIT-6", "WR-UNIT-6:child-addressed-cancel", "A", "tree", "PROC+MCP", "BOTH"
)

REPO = Path(__file__).resolve().parents[3]
TREES = REPO / "tests" / "fixtures" / "trees"
HOST_TIMEOUT_S = tolerances.JOIN_WAIT_S * 6
JOIN_MS = int(tolerances.JOIN_WAIT_S * 1000)
TERMINAL_STATES = ("succeeded", "failed", "cancelled", "timed_out")
RESTRICTED_PROFILE = '[profile]\nmode = "restricted"\nallowlist = ["exception_branch"]\n'


def _plant(host: mcp_host.McpHost, name: str) -> None:
    shutil.copy(TREES / f"{name}.py", host.home / "plugins")


def _run_dir(host: mcp_host.McpHost, run_id: str) -> Path:
    found = list((host.home / "runs").glob(f"*/{run_id}"))
    assert len(found) == 1, found
    return found[0]


def _ends(run_dir: Path) -> dict[str, dict[str, object]]:
    """Every `NodeEnd` of the run's lane, keyed by the path text (`""` is the root)."""
    lane = records.lane_rows(run_dir)
    assert not lane.problems and not lane.torn, lane.problems
    return {str(row.path): row.entry for row in lane.rows if row.cls == "end"}


def _await(host: mcp_host.McpHost, run_id: str, timeout_ms: int) -> Any:
    """The MCP `await_runs` tool over one id: the list of views (the wire wraps a list result as
    `{"result": [...]}`), or the refusal's dict."""
    got = host.call("await_runs", {"run_ids": [run_id], "timeout_ms": timeout_ms})
    if isinstance(got, dict) and set(got) == {"result"}:
        return got["result"]
    return got


def _listed(answer: dict[str, Any]) -> dict[tuple[str, ...], str]:
    return {tuple(item["path"]): item["listing"] for item in answer["listed"]}


@pytest.fixture
def mcp(tmp_path: Path) -> Iterator[mcp_host.McpHost]:
    with mcp_host.McpHost(home=tmp_path / "host-home", timeout_s=HOST_TIMEOUT_S) as host:
        yield host


@pytest.fixture
def restricted(tmp_path: Path) -> Iterator[mcp_host.McpHost]:
    """The owning session: a server whose operator profile is restricted to `exception_branch`
    (the profile is read once, at start, so the config is written first)."""
    home = tmp_path / "restricted-home"
    home.mkdir()
    (home / "config.toml").write_text(RESTRICTED_PROFILE, encoding="utf-8")
    with mcp_host.McpHost(home=home, timeout_s=HOST_TIMEOUT_S) as host:
        yield host


@proves_a63
@proves_exception
@proves_listed
def test_exception_stops_siblings_listed_in_answer(mcp: mcp_host.McpHost) -> None:
    """One `run` call on `exception_branch`: `raiser` raises while `sibling_a` and `sibling_b` are
    mid-wait. The siblings record nothing but releases after the exception, are cut `stopped`, and
    the answer names the exception as the primary and lists the siblings `stopped`."""
    _plant(mcp, "exception_branch")
    wired = hostpath.mcp_run_tree(mcp, "exception_branch", {"env": "dev"})
    assert "code" not in wired and str(wired["run_id"]).startswith("r_"), wired
    assert wired["state"] in TERMINAL_STATES, wired
    answer = wired["answer"]
    assert answer["outcome"] == "execution_error", answer
    primary = answer["primary"]
    assert primary["path"] == ["raiser"] and primary["node_class"] == "execution_error", primary
    assert primary["code"] == "execution.unit_raised", primary
    # exactly one error record: the exception's, at the raising node
    assert answer["error"]["code"] == "execution.unit_raised", answer["error"]
    assert answer["error"]["phase"] == "raiser", answer["error"]
    listed = _listed(answer)
    assert listed[("sibling_a",)] == listed[("sibling_b",)] == "stopped", listed

    # on disk: both siblings created their marker before the exception, and after the exception's
    # record no node records a non-release start or confirmation
    run_dir = _run_dir(mcp, str(wired["run_id"]))
    lane = records.lane_rows(run_dir)
    assert not lane.problems and not lane.torn, lane.problems
    raised = next(
        n
        for n, row in enumerate(lane.rows)
        if row.cls == "step" and row.entry["code"] == "execution.unit_raised"
    )
    assert lane.rows[raised].path == "raiser"
    made = {r.path for r in lane.rows if r.cls == "confirmation" and r.entry["effect"] == "up"}
    assert {"sibling_a", "sibling_b"} <= made, made
    late = [
        (row.path, row.cls, row.entry["effect"])
        for row in lane.rows[raised + 1 :]
        if row.cls in ("issue", "confirmation") and row.entry["effect"] != "stop"
    ]
    assert late == [], late
    ends = _ends(run_dir)
    assert ends["sibling_a"]["cut"] == ends["sibling_b"]["cut"] == "stopped", ends
    assert (ends["raiser"]["condition"], ends["raiser"]["cut"]) == ("failed", None), ends


@proves_a64
@proves_dependents
def test_ordinary_failure_stops_only_dependents(mcp: mcp_host.McpHost) -> None:
    """One `run` call per case on `failure_dependents`: an ordinary failure or block of `broken`
    cuts `left` and `right` (never started, listed `not_started`, no condition) while `independent`
    runs to its own terminal condition, and no whole-root stop row is written."""
    _plant(mcp, "failure_dependents")
    for mode, outcome in (("failed", "failed"), ("blocked", "blocked")):
        wired = hostpath.mcp_run_tree(mcp, "failure_dependents", {"env": "dev", "mode": mode})
        assert "code" not in wired and wired["state"] in TERMINAL_STATES, wired
        answer = wired["answer"]
        assert answer["outcome"] == outcome, (mode, answer)
        assert answer["primary"]["path"] == ["broken"], (mode, answer["primary"])
        assert answer["root_stop"] is None, (mode, answer["root_stop"])
        listed = _listed(answer)
        assert listed[("left",)] == listed[("right",)] == "not_started", (mode, listed)
        assert listed[("independent",)] == "candidate", (mode, listed)  # reached its own condition

        run_dir = _run_dir(mcp, str(wired["run_id"]))
        lane = records.lane_rows(run_dir)
        assert not lane.problems and not lane.torn, lane.problems
        for dependent in ("left", "right"):  # no start record: the only row is its own NodeEnd
            mine = [row for row in lane.rows if row.path == dependent]
            assert [row.cls for row in mine] == ["end"], (mode, dependent)
            assert (mine[0].entry["condition"], mine[0].entry["cut"]) == (None, "not_started")
        ends = _ends(run_dir)
        assert ends["broken"]["cut"] is None, (mode, ends["broken"])
        assert (ends["independent"]["condition"], ends["independent"]["cut"]) == (
            "satisfied",
            None,
        ), (mode, ends["independent"])
        stop_rows = [r for r in records.ledger_rows(run_dir).rows if r.get("kind") == "stop_row"]
        assert stop_rows == [], (mode, stop_rows)  # an ordinary failure is no whole-root stop


@proves_a63
@proves_owner
@proves_auth
def test_restricted_owner_reads_child_view(restricted: mcp_host.McpHost) -> None:
    """Two MC-12 sessions on one restricted `TRESTLE_HOME`: the session that admitted the root reads
    the root's child views (one `await_runs` call per child), and another session gets the
    existing scoped refusal, whichever vertex it names, real or not; a root view is not scoped."""
    _plant(restricted, "exception_branch")
    wired = hostpath.mcp_run_tree(restricted, "exception_branch", {"env": "dev"})
    assert "code" not in wired and wired["state"] in TERMINAL_STATES, wired
    root_id = str(wired["run_id"])
    accounts = [wired["answer"]["primary"], *wired["answer"]["listed"]]
    other = mcp_host.rejoin(restricted)
    try:
        for path in (("raiser",), ("sibling_a",), ("sibling_b",)):
            handle = answer_mod.child_handle(root_id, path)
            owned = _await(restricted, handle, JOIN_MS)
            assert isinstance(owned, list) and len(owned) == 1, (path, owned)
            view = owned[0]
            assert (view["run_id"], view["root_run_id"], view["path"]) == (
                handle,
                root_id,
                "/".join(path),
            ), view
            assert view["state"] in TERMINAL_STATES, view
            # the child view's account is the root answer's account of that node (B4-C8)
            account = next(a for a in accounts if a["path"] == list(path))
            assert view["answer"] == account, (view["answer"], account)
            if path != ("raiser",):
                assert view["disposition"] == "stopped", view

            refused = _await(other, handle, 0)
            assert isinstance(refused, dict) and refused["code"] == codes.NOT_OWNER, refused
            assert refused["retryable"] is False and refused["origin"] == "projection", refused
            assert "answer" not in refused and root_id in refused["message"], refused
        # a vertex the plan does not have reads as the same refusal to the other session
        ghost = answer_mod.child_handle(root_id, ("ghost",))
        other_ghost = _await(other, ghost, 0)
        assert isinstance(other_ghost, dict) and other_ghost["code"] == codes.NOT_OWNER
        # a root view is not scoped: unchanged from before the tree band
        root_view = _await(other, root_id, 0)
        assert isinstance(root_view, list) and root_view[0]["run_id"] == root_id, root_view
        assert "root_run_id" not in root_view[0], root_view[0]
    finally:
        other.close()


@proves_a63
@proves_cancel
@pytest.mark.gated_on("OQ-27")
def test_child_addressed_cancel(mcp: mcp_host.McpHost) -> None:
    """An MCP `cancel` naming a child handle of a live root is refused with the assumed
    `projection.cancel_not_root`: no cancel flag is written and the root run continues to its own
    terminal class (here the exception's, not `cancelled`). OQ-27 is undecided, so the claim is
    gated: a maintainer answer "act on the child" reverts this case with L.TR-2.5."""
    _plant(mcp, "exception_branch")
    started = mcp.call(
        "run",
        {
            "plugin": "exception_branch",
            "args": {"env": "dev"},
            "wait_ms": 0,
            "completion": "bounded",
        },
    )
    assert "code" not in started and str(started["run_id"]).startswith("r_"), started
    root_id = str(started["run_id"])
    assert started["state"] not in TERMINAL_STATES, started  # `raiser` pauses before it raises
    run_dir = _run_dir(mcp, root_id)
    for path in (("raiser",), ("sibling_a",)):
        refused = mcp.call("cancel", {"run_id": answer_mod.child_handle(root_id, path)})
        assert refused["code"] == codes.CANCEL_NOT_ROOT == "projection.cancel_not_root", refused
        assert (refused["retryable"], refused["origin"]) == (False, "projection"), refused
        assert root_id in refused["message"], refused
        assert not cancel_flag_path(run_dir).exists(), path  # nothing was requested of the root
    views = _await(mcp, root_id, JOIN_MS)
    assert isinstance(views, list) and len(views) == 1, views
    view = views[0]
    # its own terminal class: the exception's, not a cancel
    assert view["state"] in TERMINAL_STATES and view["state"] != "cancelled", view
    assert view["answer"]["outcome"] == "execution_error", view["answer"]
    assert view["answer"]["root_stop"] is None, view["answer"]
    assert not cancel_flag_path(run_dir).exists()
