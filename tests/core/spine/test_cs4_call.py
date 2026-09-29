"""CS-4 the call (L.CS-4.1..4.2; MC-16, SA-05, SA-12): `run(completion="terminal")` answers only
from the finalized terminal row, bounded by the run's deadline plus `clock.finalization_margin`;
the blocking work a held call once did on the event loop runs on worker threads.

Every timing bound comes from `tests.proof.tolerances` or `trestle.common.clock` (SA-05); no timing
literal appears here.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

import pytest

from tests.core.spine import support
from tests.proof import harness, mcp_host, records, tolerances
from trestle.common import clock, codes
from trestle.common.types import RequestOutcome, RunView
from trestle.server.ledger import TERMINAL_KINDS

# A plugin that runs for a couple of settle units: long enough that a bounded frame would still
# say running, short enough for a test.
WORK_S = tolerances.SETTLE_LONG_S * 2
# A run budget of one settle unit, in whole seconds.
BUDGET_S = int(tolerances.SETTLE_LONG_S)
# A wait_ms that is above zero but far too short for any run to finish inside it.
TINY_WAIT_MS = int(tolerances.POLL_FINE_S * 1000)
# A caller that asks not to wait at all.
NO_WAIT_MS = 0


def _run_dir(host: mcp_host.McpHost, run_id: str) -> Path:
    (found,) = sorted((host.home / "runs").glob(f"*/{run_id}"))
    return found


def _as_view(result: RunView | RequestOutcome) -> RunView:
    assert isinstance(result, RunView), result
    return result


# ---- L.CS-4.1: completion="terminal" --------------------------------------------------------


@pytest.mark.proves(
    "WR-TERM-2", "WR-TERM-2:response-after-terminal-row", "core", "core", "MCP", "CI"
)
@pytest.mark.proves(
    "WR-TERM-2", "WR-TERM-2:within-deadline-plus-margin", "core", "core", "MCP", "CI"
)
def test_terminal_response_follows_terminal_row(tmp_path: Path) -> None:
    with mcp_host.McpHost(home=tmp_path / "host-home") as host:
        started = time.monotonic()
        # wait_ms is far shorter than the run: a bounded call would answer `running` at once
        bounded = host.call("run", {"plugin": "slow", "args": {"seconds": WORK_S}})
        assert bounded["state"] == "running", bounded
        bounded_dir = _run_dir(host, bounded["run_id"])
        assert support.wait_until(
            lambda: records.node_record(bounded_dir).terminal is not None,
            tolerances.HARNESS_WAIT_MS / 1000,
        )

        started = time.monotonic()
        answer = host.call(
            "run",
            {
                "plugin": "slow",
                "args": {"seconds": WORK_S},
                "wait_ms": TINY_WAIT_MS,
                "completion": "terminal",
            },
        )
        elapsed = time.monotonic() - started
        answered_at = time.time()
        assert answer["state"] == "succeeded", answer
        run_dir = _run_dir(host, answer["run_id"])

        # the answer arrived only after the plugin finished, and the ledger already held the
        # finalization and then the terminal row: the response follows the row, not the reverse
        assert elapsed >= WORK_S
        kinds = records.node_record(run_dir).kinds
        assert kinds[-2:] == ["evidence_finalized", "succeeded"], kinds
        assert (run_dir / "evidence" / "ledger.ndjson").stat().st_mtime <= answered_at

        # inside the admitted deadline plus the published margin
        spec = json.loads((run_dir / "evidence" / "spec.json").read_text(encoding="utf-8"))
        budget = spec["timeout_s"] + clock.finalization_margin
        assert elapsed <= budget
        assert datetime.fromisoformat(spec["deadline"]).tzinfo is not None
        assert clock.finalization_margin >= clock.stop_bound


@pytest.mark.proves(
    "WR-COMPAT-3", "WR-COMPAT-3:bounded-default-unchanged", "core", "core", "MCP", "CI"
)
def test_bounded_default_is_unchanged(tmp_path: Path) -> None:
    with mcp_host.McpHost(home=tmp_path / "host-home") as host:
        default = host.call("run", {"plugin": "slow", "args": {"seconds": WORK_S}})
        explicit = host.call(
            "run",
            {"plugin": "slow", "args": {"seconds": WORK_S}, "completion": "bounded"},
        )
        assert default["state"] == explicit["state"] == "running"
        # the frame is the same shape: `completion` adds no key to the answer
        assert set(default) == set(explicit)
        done = host.call(
            "run",
            {"plugin": "echo", "args": {"message": "x"}, "wait_ms": tolerances.HARNESS_WAIT_MS},
        )
        assert done["state"] == "succeeded"
        # cancel both so no plugin outlives the host
        host.call("cancel", {"run_id": default["run_id"]})
        host.call("cancel", {"run_id": explicit["run_id"]})


def test_terminal_without_a_wait_is_refused_before_admission(tmp_path: Path) -> None:
    kernel = support.spine_kernel(tmp_path / "home")
    refused = kernel.control.run(
        plugin="echo", args={"message": "x"}, wait_ms=NO_WAIT_MS, completion="terminal"
    )
    assert isinstance(refused, RequestOutcome)
    assert refused.code == codes.INVALID_ARGS and refused.origin == "admission"
    unknown = kernel.control.run(plugin="echo", args={"message": "x"}, completion="eventually")
    assert isinstance(unknown, RequestOutcome) and unknown.code == codes.INVALID_ARGS
    # a refusal is not a run: nothing was admitted
    assert not list((kernel.home / "runs").glob("*/r_*"))


@pytest.mark.proves(
    "WR-TERM-2", "WR-TERM-2:budget-edge-never-running", "core", "core", "PROC", "CI"
)
def test_finishing_exactly_at_budget_never_running() -> None:
    kernel = support.spine_kernel()
    # the plugin's own work is the run's whole budget, so it races its deadline: whichever wins,
    # the answer is a terminal one and its row is already in the ledger
    with harness.patch_snapshot(kernel, "slow", timeout_s=BUDGET_S):
        view = _as_view(
            kernel.control.run(
                plugin="slow",
                args={"seconds": float(BUDGET_S)},
                wait_ms=BUDGET_S * 1000,
                completion="terminal",
            )
        )
    assert view.state in TERMINAL_KINDS, view.state
    assert view.state in {"succeeded", "timed_out"}
    terminal = records.node_record(support.run_dir_of(kernel, view.run_id)).terminal
    assert terminal == view.state


def test_sigterm_ignoring_plugin_at_deadline_answers_timed_out() -> None:
    kernel = support.spine_kernel()
    started = time.monotonic()
    with harness.patch_snapshot(kernel, "sigterm_ignorer", timeout_s=BUDGET_S):
        answer = kernel.control.run(
            plugin="sigterm_ignorer", wait_ms=BUDGET_S * 1000, completion="terminal"
        )
    elapsed = time.monotonic() - started
    # the stop needed SIGKILL after the whole grace, and the answer still came inside the bound:
    # a terminal timed_out, never the wait's own failure code (PC-19)
    view = _as_view(answer)
    assert view.state == "timed_out"
    assert elapsed <= BUDGET_S + clock.finalization_margin
    assert elapsed >= BUDGET_S + clock.grace
    run_dir = support.run_dir_of(kernel, view.run_id)
    assert support.kinds(run_dir)[-2:] == ["evidence_finalized", "timed_out"]


def test_wait_past_the_bound_is_the_named_code_never_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kernel = support.spine_kernel()
    monkeypatch.setattr(clock, "finalization_margin", 0.0)  # the bound is the deadline itself
    with harness.patch_snapshot(kernel, "slow", timeout_s=BUDGET_S):
        answer = kernel.control.run(plugin="slow", wait_ms=BUDGET_S * 1000, completion="terminal")
    assert isinstance(answer, RequestOutcome), answer
    assert answer.code == codes.TERMINAL_WAIT_EXCEEDED and answer.origin == "projection"
    assert "running" not in answer.to_dict()
    # the run is still there and reaches its terminal row on its own
    (run_dir,) = sorted(kernel.home.glob("runs/*/r_*"))
    assert support.wait_until(
        lambda: records.node_record(run_dir).terminal is not None,
        tolerances.HARNESS_WAIT_MS / 1000,
    )
