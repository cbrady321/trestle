"""CS-4 the call (L.CS-4.1..4.2; MC-16, SA-05, SA-12): `run(completion="terminal")` answers only
from the finalized terminal row, bounded by the run's deadline plus `clock.finalization_margin`;
the blocking work a held call once did on the event loop runs on worker threads.

Every timing bound comes from `tests.proof.tolerances` or `trestle.common.clock` (SA-05); no timing
literal appears here.
"""

from __future__ import annotations

import asyncio
import json
import queue
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

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
        assert (
            records.await_terminal(bounded_dir, bound_s=tolerances.HARNESS_WAIT_MS / 1000).why
            == "condition"
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
    assert (
        records.await_terminal(run_dir, bound_s=tolerances.HARNESS_WAIT_MS / 1000).why
        == "condition"
    )


# ---- L.CS-4.2: no blocking work on the event loop while a call is held ----------------------

# A plugin that outlives every check below; each run is cancelled before its test ends.
LONG_S = tolerances.JOIN_WAIT_S * 6


def _run_ids(host: mcp_host.McpHost) -> list[str]:
    return sorted(path.name for path in (host.home / "runs").glob("*/r_*"))


@pytest.mark.proves(
    "WR-TERM-7", "WR-TERM-7:held-wait-cancel-query-bound", "core", "core", "MCP", "CI"
)
def test_cancel_and_query_answer_while_terminal_call_held(tmp_path: Path) -> None:
    with mcp_host.McpHost(home=tmp_path / "host-home") as host:
        other = host.call("run", {"plugin": "slow", "args": {"seconds": LONG_S}})["run_id"]
        held = host.hold(
            "run",
            {
                "plugin": "slow",
                "args": {"seconds": LONG_S},
                "completion": "terminal",
                "wait_ms": tolerances.HARNESS_WAIT_MS,
            },
        )
        assert support.wait_until(lambda: len(_run_ids(host)) == 2, tolerances.JOIN_WAIT_S), (
            "the held call never admitted its run"
        )
        (held_run,) = [run_id for run_id in _run_ids(host) if run_id != other]

        # the other run's calls are answered while the held call is still waiting
        answered = host.call("query", {"view": "run", "params": {"run_id": other}})
        assert answered["items"][0]["run_id"] == other
        cancelled_at = time.monotonic()
        assert host.call("cancel", {"run_id": other})["code"] == codes.CANCEL_ACCEPTED
        with pytest.raises(queue.Empty):  # neither answer was the held call's
            host.join(held, timeout=tolerances.SETTLE_SHORT_S)

        # the cancel takes effect within the stop bound, the supervisor's poll and a tolerance
        other_dir = _run_dir(host, other)
        bound = clock.stop_bound + clock.poll_interval + tolerances.SETTLE_LONG_S
        assert records.await_terminal(other_dir, bound_s=bound).why == "condition"
        assert time.monotonic() - cancelled_at <= bound
        assert records.node_record(other_dir).terminal == "cancelled"

        # the held call is still waiting on its own run; cancelling that run releases it
        assert host.call("cancel", {"run_id": held_run})["code"] == codes.CANCEL_ACCEPTED
        released = host.join(held, timeout=tolerances.HARNESS_WAIT_MS / 1000)
        assert released["state"] == "cancelled" and released["run_id"] == held_run


def _slow_first_call(
    target: object, name: str, seen: set[int], pause_s: float
) -> Callable[..., Any]:
    """`target.<name>` wrapped to note the thread it runs on and to block for `pause_s` first, the
    way a slow ledger read or a slow admission would."""
    real = getattr(target, name)

    def wrapped(*args: Any, **kwargs: Any) -> Any:
        seen.add(threading.get_ident())
        time.sleep(pause_s)
        return real(*args, **kwargs)

    return wrapped


@pytest.mark.parametrize("blocking", ["admit", "status"])
def test_admit_and_status_polls_do_not_block_the_event_loop(
    blocking: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    kernel = support.spine_kernel()
    seen: set[int] = set()
    target = kernel.control.admission if blocking == "admit" else kernel.control.project
    monkeypatch.setattr(
        target, blocking, _slow_first_call(target, blocking, seen, tolerances.SETTLE_LONG_S)
    )

    async def scenario() -> tuple[float, int, RunView | RequestOutcome]:
        gaps: list[float] = []
        done = asyncio.Event()

        async def ticker() -> None:  # what any other call on the loop would experience
            last = time.monotonic()
            while not done.is_set():
                await asyncio.sleep(tolerances.POLL_FINE_S)
                now = time.monotonic()
                gaps.append(now - last)
                last = now

        beat = asyncio.create_task(ticker())
        answer = await kernel.control.run_async(
            plugin="echo", args={"message": "x"}, wait_ms=tolerances.HARNESS_WAIT_MS
        )
        done.set()
        await beat
        return max(gaps), threading.get_ident(), answer

    longest_gap, loop_thread, answer = asyncio.run(scenario())
    assert isinstance(answer, RunView) and answer.state == "succeeded"
    assert seen and loop_thread not in seen  # the blocking call ran on a worker thread
    assert longest_gap < tolerances.SETTLE_LONG_S / 2  # and the loop kept turning meanwhile


def test_concurrent_admissions_stay_serialized() -> None:
    """Off the loop, two admissions with one idempotency key still mint one run: admit is one at
    a time, as the loop's single thread made it before."""
    kernel = support.spine_kernel()

    async def scenario() -> list[RunView | RequestOutcome]:
        return list(
            await asyncio.gather(
                *(
                    kernel.control.run_async(
                        plugin="echo",
                        args={"message": "x"},
                        wait_ms=tolerances.HARNESS_WAIT_MS,
                        idempotency_key="cs4-one-key",
                    )
                    for _ in range(3)
                )
            )
        )

    answers = asyncio.run(scenario())
    views = [_as_view(answer) for answer in answers]
    assert len({view.run_id for view in views}) == 1
