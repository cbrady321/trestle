"""L.TR-L.3: the WR-TERM-1 three-step fixture, through one MCP request (the host re-run).

`three_step_live` (the behaviour twin of the structural `three_step_retry_remedy`, MC-B3-01) is
`build` -> `deploy` -> `verify`, one at a time; `deploy` has its first create refused with a
declared-retryable code (the retry) and its marker reports the declared remedy trigger until it is
restarted (the remediation). Multi-vertex roots were refused before L.TR-L.1, so this is the first
time the whole workflow runs as one call.

The call is the MCP `run` tool on a `trestle serve` subprocess with `completion="terminal"`
(MC-16), counted by the MC-12 host: exactly one request, no polling and no second call, and it
outlives the default bounded wait several times over (MC-09: no timing literal here, the bound is
read from the call's own signature)."""

from __future__ import annotations

import inspect
import shutil
import time
from pathlib import Path
from typing import Any

import pytest

from tests.fixtures.trees import three_step_live
from tests.proof import mcp_host, records, tolerances
from tests.tree import hostpath
from trestle.common import clock
from trestle.server.control import ControlSurface
from trestle.server.ledger import TERMINAL_KINDS

REPO = Path(__file__).resolve().parents[3]
TREES = REPO / "tests" / "fixtures" / "trees"
PLUGIN = "three_step_live"

# MC-16: `run`'s default bounded wait, in seconds, read from the surface's own default.
BOUNDED_WAIT_S = inspect.signature(ControlSurface.run).parameters["wait_ms"].default / 1000
# The call names its own bound (the tree's deadline, plus the margin the terminal wait adds); the
# host waits a little longer than the call does.
CALL_WAIT_S = three_step_live.DEADLINE_S + clock.finalization_margin
HOST_TIMEOUT_S = CALL_WAIT_S + tolerances.JOIN_WAIT_S

proves_term1 = pytest.mark.proves(
    "WR-TERM-1", "WR-TERM-1:3-step-one-call", "A", "tree", "MCP", "CI"
)


@pytest.fixture
def mcp(tmp_path: Path) -> Any:
    with mcp_host.McpHost(home=tmp_path / "host-home", timeout_s=HOST_TIMEOUT_S) as host:
        shutil.copy(TREES / f"{PLUGIN}.py", host.home / "plugins")
        yield host


def _tickets(run_dir: Path, unit: str) -> list[dict[str, Any]]:
    """`unit`'s recorded attempts in lane order: one per issued effect call, with its issue entry
    and the confirmation that named it."""
    lane = records.lane_rows(run_dir)
    return [t for t in records.lane_tickets(lane) if t["issue"].entry["path"] == unit]


def _attempts(run_dir: Path, unit: str, effect: str) -> list[tuple[int, str | None]]:
    """(attempt number, confirmation code) of every call of `effect` `unit` issued."""
    return [
        (
            t["issue"].entry["attempt"],
            t["confirmation"].entry["code"] if t["confirmation"] else None,
        )
        for t in _tickets(run_dir, unit)
        if t["issue"].entry["effect"] == effect
    ]


@proves_term1
def test_three_step_retry_remedy_one_call(mcp: mcp_host.McpHost) -> None:
    """One `run` call carries the whole three-step workflow, retry and remediation included, to a
    terminal answer: the run outlives the default bounded wait at least three times over, the host
    counted exactly one request, and the answer is class `passed` with disposition `repaired`."""
    requests_before = mcp.request_count()
    started = time.monotonic()
    answer = hostpath.mcp_run_tree(mcp, PLUGIN, {"env": "dev"}, wait_ms=int(CALL_WAIT_S * 1000))
    elapsed = time.monotonic() - started

    # MC-12: one tool request, and nothing else: no poll, no await_runs, no query
    assert mcp.request_count() - requests_before == 1

    # it was not a quick call the bounded default would have answered
    assert elapsed >= 3 * BOUNDED_WAIT_S, (elapsed, BOUNDED_WAIT_S)

    # the frame is the terminal one, admitted as one run, with its terminal row already written
    assert "code" not in answer and str(answer["run_id"]).startswith("r_"), answer
    assert answer["state"] == "succeeded", answer
    run_dirs = sorted((mcp.home / "runs").glob("*/r_*"))
    assert [d.name for d in run_dirs] == [answer["run_id"]]
    assert records.node_record(run_dirs[0]).terminal in TERMINAL_KINDS

    # class passed with disposition repaired (DM-03: `repaired` is a disposition, not a class):
    # the root's class is `passed`, and the node that was remedied, `deploy`, is the one reported
    # repaired; its neighbours passed untouched
    tree = answer["answer"]
    assert tree["outcome"] == "passed" and tree["root_stop"] is None and tree["error"] is None
    assert tree["primary"]["path"] == ["deploy"]
    assert tree["primary"]["disposition"] == "repaired"
    listed = {tuple(n["path"]): n for n in tree["listed"] if n["listing"] == "candidate"}
    assert {p: n["node_class"] for p, n in listed.items()} == {
        ("build",): "passed",
        ("verify",): "passed",
    }
    assert all(n["condition"] == "satisfied" for n in listed.values())

    # the retry and the remediation really happened, in the recorded attempts of `deploy`: the
    # first create was refused with the retryable code and issued again, and one restart was made
    run_dir = run_dirs[0]
    assert _attempts(run_dir, "deploy", "up") == [(1, three_step_live.TRANSIENT), (2, None)]
    assert _attempts(run_dir, "deploy", "restart") == [(1, None)]
    for unit in ("build", "verify"):  # the others took one create and no remedy
        assert _attempts(run_dir, unit, "up") == [(1, None)]
        assert _attempts(run_dir, unit, "restart") == []
