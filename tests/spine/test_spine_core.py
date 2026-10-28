"""The spine gate (L.CS-4.4; MC-29): the unattended one-call acceptance of the core corrections,
run by CI job `spine` (`pytest -m spine`) under gate `ci-spine`.

Each test is one walk of the design's section 6.1, driven through the MC-12 MCP host, i.e. the
same tools an agent calls: W-0 (a plugin raises under `completion="terminal"`), W-0b (a cancel
reaches the whole tree), W-0c (the server is killed mid-run and restarts), the deadline stop, and
the contract anchors (ten tools within the byte budget, `completion` documented in the schema).
The planted-defect test proves the gate can fail: with the child's own session restored, the
W-0b oracle refuses.

Later phases extend this file (SV-5, TR-6, RB-0). Every timing bound comes from
`tests.proof.tolerances` or `trestle.common.clock` (SA-05); no timing literal appears here.
"""

from __future__ import annotations

import json
import os
import shutil
import textwrap
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from tests.core.spine import support
from tests.proof import ancestry, harness, mcp_host, records, tolerances
from trestle.common import clock, codes
from trestle.common.types import RunView

pytestmark = pytest.mark.spine

TOOLS_LIST_BYTE_BUDGET = 8192
TEN_TOOLS = {
    "run",
    "await_runs",
    "cancel",
    "query",
    "fetch",
    "pin",
    "unpin",
    "list_plugins",
    "describe_plugin",
    "publish_plugin",
}
# A plugin that outlives every check; each run is ended by the test before it finishes.
LONG_S = tolerances.JOIN_WAIT_S * 6

# The planted defect: a sitecustomize that gives the run's child a session of its own again, as
# the code did before DD-1(b) (design section 8.2: the recorded group no longer covers the tree).
_PLANTED_SITECUSTOMIZE = textwrap.dedent(
    """
    import subprocess

    _real_init = subprocess.Popen.__init__

    def _init(self, args, *a, **kw):
        if isinstance(args, (list, tuple)) and "trestle.child.main" in args:
            kw["start_new_session"] = True
        _real_init(self, args, *a, **kw)

    subprocess.Popen.__init__ = _init
    """
)


def _seed(host: mcp_host.McpHost, *names: str) -> None:
    for name in names:
        shutil.copy(support.SPINE_PLUGIN_DIR / f"{name}.py", host.home / "plugins")


def _run_dir(host: mcp_host.McpHost, run_id: str) -> Path:
    (found,) = sorted((host.home / "runs").glob(f"*/{run_id}"))
    return found


def _await(host: mcp_host.McpHost, run_id: str) -> dict[str, Any]:
    answer = host.call(
        "await_runs",
        {"run_ids": [run_id], "mode": "all", "timeout_ms": tolerances.HARNESS_WAIT_MS},
    )
    views = answer["result"] if isinstance(answer, dict) and "result" in answer else answer
    (view,) = views
    assert isinstance(view, dict), view
    return view


@contextmanager
def _host(tmp_path: Path) -> Iterator[mcp_host.McpHost]:
    with mcp_host.McpHost(home=tmp_path / "host-home") as host:
        yield host


def _tree_of(run_id: str) -> support.Tree:
    return support.Tree(marker=run_id, live=support.marked(run_id))


# ---- W-0: a dict-annotated plugin raises, under completion="terminal" -----------------------


def test_w0_plugin_raises_under_terminal_completion(tmp_path: Path) -> None:
    with _host(tmp_path) as host:
        _seed(host, "raiser")
        answer = host.call(
            "run",
            {"plugin": "raiser", "wait_ms": tolerances.HARNESS_WAIT_MS, "completion": "terminal"},
        )
        assert answer["state"] == "failed", answer
        run_dir = _run_dir(host, answer["run_id"])

        # the answer follows the finalized terminal row, never the reverse
        assert support.kinds(run_dir)[-2:] == ["evidence_finalized", "failed"]
        # one class from the closed set, with the error decidable from the answer alone
        result = answer["outcome"]
        assert result["class"] == "execution_error"
        assert result["code"] == codes.EXECUTION_PLUGIN_RAISED
        assert result["recovered"] is False
        assert answer["error"]["code"] == codes.EXECUTION_PLUGIN_RAISED
        assert answer["error"]["message"]
        # the run's one error row is the authority for the explanation (U3)
        assert [r["code"] for r in support.rows_of(run_dir, "error_record")] == [
            codes.EXECUTION_PLUGIN_RAISED
        ]


# ---- W-0b: a cancel reaches the tree --------------------------------------------------------


def _assert_cancel_reaches_tree(host: mcp_host.McpHost, run_id: str) -> None:
    """The W-0b oracle: the child keeps the wrapper's group (the recorded group covers the tree),
    a cancel ends every process of the tree inside the stop bound, and the answer is `cancelled`
    with a cleanup disposition and a confirmed-gone stop record."""
    run_dir = _run_dir(host, run_id)
    support.wait_ready(run_dir)
    tree = _tree_of(run_id)
    (wrapper,) = tree.wrapper
    (child,) = tree.child
    assert child.pgid == wrapper.pgid, "the child left the wrapper's group: containment is lost"
    assert host.call("cancel", {"run_id": run_id})["code"] == codes.CANCEL_ACCEPTED
    view = _await(host, run_id)
    assert view["state"] == "cancelled" and view["outcome"]["class"] == "cancelled", view
    assert view["cleanup"], view
    assert support.wait_until(
        lambda: not support.alive_marked(tree.live, run_id),
        clock.stop_bound + tolerances.PROC_WAIT_S,
    ), "a process of the run's tree outlived the stop bound"
    (stop,) = support.rows_of(run_dir, "group_stop")
    assert stop["confirmed_gone"] is True, stop


def test_w0b_cancel_reaches_the_tree(tmp_path: Path) -> None:
    with _host(tmp_path) as host:
        _seed(host, "tree")
        run_id = host.call("run", {"plugin": "tree", "args": {"seconds": LONG_S}, "wait_ms": 0})[
            "run_id"
        ]
        try:
            _assert_cancel_reaches_tree(host, run_id)
        finally:
            ancestry.reap(support.marked(run_id))


def test_spine_detects_planted_containment_defect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gate can fail: with the child's own session restored, W-0b's oracle refuses."""
    plant = tmp_path / "planted"
    plant.mkdir()
    (plant / "sitecustomize.py").write_text(_PLANTED_SITECUSTOMIZE, encoding="utf-8")
    existing = os.environ.get("PYTHONPATH", "")
    monkeypatch.setenv("PYTHONPATH", f"{plant}{os.pathsep}{existing}" if existing else str(plant))
    with _host(tmp_path) as host:
        _seed(host, "tree")
        run_id = host.call("run", {"plugin": "tree", "args": {"seconds": LONG_S}, "wait_ms": 0})[
            "run_id"
        ]
        try:
            with pytest.raises(AssertionError, match="left the wrapper's group"):
                _assert_cancel_reaches_tree(host, run_id)
        finally:
            ancestry.reap(support.marked(run_id))


# ---- W-0c: the server is killed mid-run and restarts ----------------------------------------


def test_w0c_server_killed_mid_run_restarts_interrupted(tmp_path: Path) -> None:
    with _host(tmp_path) as host:
        _seed(host, "tree")
        run_id = host.call("run", {"plugin": "tree", "args": {"seconds": LONG_S}, "wait_ms": 0})[
            "run_id"
        ]
        try:
            run_dir = _run_dir(host, run_id)
            support.wait_ready(run_dir)
            tree = _tree_of(run_id)
            host.kill_server()
            host.restart()
            view = _await(host, run_id)
            # reported as interrupted, never as success, and recovery stopped the tree first
            assert view["state"] == "interrupted", view
            assert view["outcome"]["class"] == "execution_error"
            assert view["outcome"]["code"] == codes.EXECUTION_INTERRUPTED
            assert view["outcome"]["recovered"] is True
            assert support.wait_until(
                lambda: not support.alive_marked(tree.live, run_id),
                clock.stop_bound + tolerances.PROC_WAIT_S,
            ), "recovery finalized the run with its tree still alive"
            kinds = support.kinds(run_dir)
            assert kinds[-1] == "interrupted" and "evidence_finalized" in kinds
        finally:
            ancestry.reap(support.marked(run_id))


# ---- the deadline stop ----------------------------------------------------------------------


def test_deadline_stop_answers_timed_out_within_bound() -> None:
    """A plugin that ignores its budget is stopped at the run's deadline; the one call answers
    `timed_out` inside deadline plus margin. In-process kernel, PROC tier: through MCP the plugin
    budget is fixed (no knob), so the deadline is set on the snapshot (as in test_cs4_call)."""
    kernel = support.spine_kernel()
    with harness.patch_snapshot(kernel, "slow", timeout_s=support.SHORT_DEADLINE_S):
        started = time.monotonic()
        answer = kernel.control.run(
            plugin="slow",
            args={"seconds": LONG_S},
            wait_ms=support.SHORT_DEADLINE_S * 1000,
            completion="terminal",
        )
        elapsed = time.monotonic() - started
    assert isinstance(answer, RunView), answer
    with support.reaping(answer.run_id):
        assert answer.state == "timed_out"
        assert answer.to_dict()["outcome"]["class"] == "timed_out"
        assert elapsed <= support.SHORT_DEADLINE_S + clock.finalization_margin
        run_dir = support.run_dir_of(kernel, answer.run_id)
        assert support.kinds(run_dir)[-2:] == ["evidence_finalized", "timed_out"]
        assert records.node_record(run_dir).terminal == "timed_out"


# ---- the contract anchors, through one call -------------------------------------------------


@pytest.mark.proves(
    "WR-COMPAT-1",
    "WR-COMPAT-1:ten-tools-within-budget",
    "core",
    "core",
    "MCP",
    "CI",
)
def test_contract_anchors_ten_tools_and_completion_in_one_call(tmp_path: Path) -> None:
    with _host(tmp_path) as host:
        raw = host.tools_list_raw()
        tools = json.loads(raw)["result"]["tools"]
        assert {tool["name"] for tool in tools} == TEN_TOOLS
        # the definitions an agent reads stay within the byte budget with `completion` added
        definitions = json.dumps(
            [
                {
                    "name": t["name"],
                    "description": t["description"],
                    "inputSchema": t["inputSchema"],
                }
                for t in tools
            ],
            separators=(",", ":"),
        )
        assert len(definitions.encode("utf-8")) <= TOOLS_LIST_BYTE_BUDGET
        run_tool = next(tool for tool in tools if tool["name"] == "run")
        assert "completion" in run_tool["inputSchema"]["properties"]
        assert "completion" not in run_tool["inputSchema"].get("required", [])

        # one call: the terminal answer to an ordinary plugin, from the one answer alone
        answer = host.call(
            "run",
            {
                "plugin": "echo",
                "args": {"message": "spine"},
                "wait_ms": tolerances.HARNESS_WAIT_MS,
                "completion": "terminal",
            },
        )
        assert answer["state"] == "succeeded" and answer["outcome"]["class"] == "passed"
