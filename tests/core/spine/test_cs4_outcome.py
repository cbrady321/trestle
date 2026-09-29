"""CS-4 outcome class (L.CS-4.3; MC-17, B4-T4, DM-03): each of the six plain-plugin conditions
lands in exactly one class of the closed set, and the one answer (`RunView`) carries what that
class requires: the code (`None` for cancelled and timed_out), the phase and a bounded message for
an execution error, the cleanup disposition for cancelled and timed_out. `state` keeps its
meaning underneath the class (WR-COMPAT-8)."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest

from tests.core.spine import support
from tests.proof import ancestry, harness, mcp_host, tolerances
from trestle.common import clock, codes, errtext, outcome
from trestle.common.types import RequestOutcome, RunView

CONDITIONS = (
    "pass",
    "cancel",
    "deadline",
    "uncaught_exception",
    "unencodable_result",
    "restart_mid_run",
)
# B4-T4, transcribed: condition -> (run state, class, code)
EXPECTED: dict[str, tuple[str, str, str | None]] = {
    "pass": ("succeeded", "passed", None),
    "cancel": ("cancelled", "cancelled", None),
    "deadline": ("timed_out", "timed_out", None),
    "uncaught_exception": ("failed", "execution_error", codes.EXECUTION_PLUGIN_RAISED),
    "unencodable_result": ("failed", "execution_error", codes.EXECUTION_RESULT_UNENCODABLE),
    "restart_mid_run": ("interrupted", "execution_error", codes.EXECUTION_INTERRUPTED),
}


def _wire(view: RunView | RequestOutcome) -> dict[str, Any]:
    assert isinstance(view, RunView), view
    return view.to_dict()


def _await_wire(kernel: Any, run_id: str) -> dict[str, Any]:
    return _wire(kernel.control.project.await_one(run_id, tolerances.HARNESS_WAIT_MS * 3))


def _restart_mid_run(tmp_path: Path) -> dict[str, Any]:
    with mcp_host.McpHost(home=tmp_path / "host-home") as host:
        run_id = ""
        try:
            shutil.copy(support.SPINE_PLUGIN_DIR / "tree.py", host.home / "plugins")
            run_id = host.call("run", {"plugin": "tree", "wait_ms": 0})["run_id"]
            (run_dir,) = sorted((host.home / "runs").glob(f"*/{run_id}"))
            support.wait_ready(run_dir)
            host.kill_server()
            host.restart()
            answer = host.call(
                "await_runs",
                {"run_ids": [run_id], "mode": "all", "timeout_ms": tolerances.HARNESS_WAIT_MS},
            )
            views = answer["result"] if isinstance(answer, dict) and "result" in answer else answer
            (view,) = views
            assert isinstance(view, dict)
            return view
        finally:
            if run_id:
                ancestry.reap(support.marked(run_id))


@pytest.fixture(scope="module")
def answers(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict[str, Any]]:
    """The one answer of each of the six conditions, produced once for the module."""
    mp = pytest.MonkeyPatch()
    mp.setattr(clock, "grace", tolerances.SETTLE_SHORT_S)
    mp.setattr(clock, "kill", tolerances.SETTLE_SHORT_S)
    try:
        kernel = support.spine_kernel(home=tmp_path_factory.mktemp("home"))
        out: dict[str, dict[str, Any]] = {}
        out["pass"] = _wire(
            kernel.control.run(
                plugin="echo", args={"message": "ok"}, wait_ms=tolerances.HARNESS_WAIT_MS
            )
        )
        out["uncaught_exception"] = _wire(
            kernel.control.run(plugin="raiser", wait_ms=tolerances.HARNESS_WAIT_MS)
        )
        out["unencodable_result"] = _wire(
            kernel.control.run(plugin="unencodable", wait_ms=tolerances.HARNESS_WAIT_MS)
        )

        started = kernel.control.run(plugin="tree", args={"seconds": 60}, wait_ms=0)
        assert isinstance(started, RunView)
        with support.reaping(started.run_id):
            support.wait_ready(support.run_dir_of(kernel, started.run_id))
            kernel.control.cancel(started.run_id)
            out["cancel"] = _await_wire(kernel, started.run_id)

        with harness.patch_snapshot(kernel, "slow", timeout_s=support.SHORT_DEADLINE_S):
            timed = kernel.control.run(plugin="slow", args={"seconds": 60}, wait_ms=0)
            assert isinstance(timed, RunView)
            with support.reaping(timed.run_id):
                out["deadline"] = _await_wire(kernel, timed.run_id)

        out["restart_mid_run"] = _restart_mid_run(tmp_path_factory.mktemp("restart"))
        return out
    finally:
        mp.undo()


@pytest.mark.proves("A1.3", "A1.3:core", "A", "core", "MCP+PROC", "CI")
@pytest.mark.proves(
    "WR-TERM-3", "WR-TERM-3:six-plain-conditions-one-class", "core", "core", "MCP+PROC", "CI"
)
def test_six_plain_conditions_each_exactly_one_class(answers: dict[str, dict[str, Any]]) -> None:
    assert set(answers) == set(CONDITIONS)
    seen_classes: set[str] = set()
    for condition, (state, klass, code) in EXPECTED.items():
        view = answers[condition]
        assert view["state"] == state, (condition, view)  # state keeps its meaning (WR-COMPAT-8)
        result = view["outcome"]
        assert set(result) == {"class", "code", "identity", "recovered"}, condition
        assert result["class"] in outcome.OUTCOME_CLASSES  # exactly one, from the closed set
        assert (result["class"], result["code"]) == (klass, code), (condition, result)
        assert result["recovered"] is (condition == "restart_mid_run"), condition
        seen_classes.add(result["class"])
    # the six conditions cover four of the six classes: failed and blocked are workflow-result
    # conditions no plain plugin reaches (B4-T4 has no row for them)
    assert seen_classes == {"passed", "cancelled", "timed_out", "execution_error"}
    # the identity is the snapshot the run's spec fixed at admission, one per run
    ids = {view["outcome"]["identity"]["snapshot_id"] for view in answers.values()}
    assert all(isinstance(i, str) and i for i in ids)


@pytest.mark.proves(
    "WR-TERM-3", "WR-TERM-3:required-fields-decidable-alone", "core", "core", "MCP+PROC", "CI"
)
def test_required_fields_are_decidable_from_the_one_answer(
    answers: dict[str, dict[str, Any]],
) -> None:
    for condition, view in answers.items():
        klass, code = view["outcome"]["class"], view["outcome"]["code"]
        if klass in {"cancelled", "timed_out"}:
            assert code is None, condition  # B4-T4
            assert view["cleanup"] == {"processes": "released"}, condition  # cleanup disposition
            assert "error" in view and view["error"]["code"] in codes.EXECUTION_CODES
        elif klass == "passed":
            assert code is None and "error" not in view, condition
        else:
            assert klass == "execution_error"
            assert code in codes.EXECUTION_CODES, condition
            error = view["error"]  # phase and a bounded message
            assert error["phase"] and error["message"], condition
            assert len(error["message"].encode("utf-8")) <= errtext.MESSAGE_MAX, condition
    # a non-terminal run has no class yet
    kernel = support.spine_kernel()
    started = kernel.control.run(plugin="slow", args={"seconds": 60}, wait_ms=0)
    assert isinstance(started, RunView)
    with support.reaping(started.run_id):
        assert started.state in {"queued", "running"} and started.outcome is None
        assert "outcome" not in started.to_dict()
        kernel.control.cancel(started.run_id)
        _await_wire(kernel, started.run_id)


def test_classify_is_total_over_terminal_kinds_and_refuses_others() -> None:
    from trestle.server.ledger import TERMINAL_KINDS

    for kind in TERMINAL_KINDS:
        result = outcome.classify(kind, None)
        assert result.outcome_class in outcome.OutcomeClass
    with pytest.raises(ValueError):
        outcome.classify("running", None)


def test_classify_code_follows_the_recorded_row_else_the_terminal_kind() -> None:
    row = {"code": codes.EXECUTION_BIND_FAILED, "phase": "bind", "message": "m"}
    assert outcome.classify("failed", row).code == codes.EXECUTION_BIND_FAILED
    assert outcome.classify("failed", None).code == codes.EXECUTION_PLUGIN_RAISED
    assert outcome.classify("failed", {"code": "not.a.code"}).code == codes.EXECUTION_PLUGIN_RAISED
    assert outcome.classify("worker_exit", None).code == codes.EXECUTION_WORKER_EXIT
    assert outcome.classify("crashed", None).code == codes.EXECUTION_WORKER_EXIT
    # a restart is always the restart code, whatever an earlier row said (B4-C2 rule (2))
    restarted = outcome.classify("interrupted", row)
    assert (restarted.code, restarted.recovered) == (codes.EXECUTION_INTERRUPTED, True)
    # cancelled and timed_out carry no code even when an error row exists (B4-T4)
    cancelled = {"code": codes.EXECUTION_CANCELLED, "phase": "stop", "message": "m"}
    assert outcome.classify("cancelled", cancelled).code is None
    assert outcome.classify("timed_out", cancelled).code is None
