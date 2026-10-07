"""L.SL-1.1: sever and rejoin (A1.2, WR-TERM-6), through the MC-12 MCP host.

A severed call never cancels the run: it ends by its own deadline inside deadline + finalization
margin with its created marker released, and an identical re-send (same key, same intent) joins that
one execution and returns its terminal answer, never a running frame. Whether a host stop also
cancels is open (row default: no); the `cancel` tool stays the explicit path, so no stop row of
cause `cancel` appears in any sever mode.

The run is `spine_leaf` in `stall` mode (its marker never turns ready and its first observation
takes `STALL_S`, so the wait, which fits the leaf's budget (L.SL-2.1), starts late and only the
deadline ends it), with the deadline shortened to `SHORT_DEADLINE_S`. Each sever mode of the host is
exercised: `cancel_notification` (the JSON-RPC notice; the server stays up), `stdin_close` (EOF on
the server's read loop) and `sigkill` (the server is gone). Where the server is gone, the run's own
process tree ends by itself at the release point (the loop releases its marker) and the next server
on the same home recovers the run to a terminal row. The lane is read through the proof court's
oracle (`tests.proof.records`); every bound comes from `tests.proof.tolerances` or
`trestle.common.clock`.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from tests.core.spine import support
from tests.proof import mcp_host, records, tolerances
from trestle.common import clock

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "workflows"
FIXTURE = "spine_leaf"
DECLARED_DEADLINE_S = 120  # the fixture's own literal; the tests substitute the shorter one
SHORT_DEADLINE_S = 20  # budget 8 + release slice 10 fit inside it (as in test_w_a1's deadline run)
KEY = "sever-rejoin-key"
ARGS: dict[str, Any] = {"env": "dev", "mode": "stall"}
NONTERMINAL = ("queued", "running")
# a call that waits for its terminal answer waits out the deadline and the finalization margin
BOUND_S = float(SHORT_DEADLINE_S) + clock.finalization_margin
HOST_TIMEOUT_S = BOUND_S + tolerances.JOIN_WAIT_S
# a run's admission, first process and first effect under a loaded host (the full suite runs 8-wide)
STARTUP_WAIT_S = tolerances.JOIN_WAIT_S * 3
SEVER_MODES = ("cancel_notification", "stdin_close", "sigkill")


def _request(*, wait_s: float = BOUND_S) -> dict[str, Any]:
    return {
        "plugin": FIXTURE,
        "args": ARGS,
        "wait_ms": int(wait_s * 1000),
        "completion": "terminal",
        "idempotency_key": KEY,
    }


def _source() -> str:
    source = (FIXTURES / f"{FIXTURE}.py").read_text(encoding="utf-8")
    source = source.replace(f"deadline={DECLARED_DEADLINE_S}", f"deadline={SHORT_DEADLINE_S}")
    return source.replace(f"seconds={DECLARED_DEADLINE_S}", f"seconds={SHORT_DEADLINE_S}")


@contextmanager
def _host(tmp_path: Path) -> Iterator[mcp_host.McpHost]:
    with mcp_host.McpHost(home=tmp_path / "host-home", timeout_s=HOST_TIMEOUT_S) as host:
        (host.home / "plugins" / f"{FIXTURE}.py").write_text(_source(), encoding="utf-8")
        yield host


def _only_run_dir(host: mcp_host.McpHost) -> Path:
    (found,) = sorted((host.home / "runs").glob("*/r_*"))
    return found


def _created_rows(run_dir: Path) -> int:
    return support.kinds(run_dir).count("created")


def _terminal(run_dir: Path) -> str | None:
    return records.node_record(run_dir).terminal


def _issued(run_dir: Path) -> bool:
    return "confirmation" in {row.cls for row in records.lane_rows(run_dir).rows}


def _released(run_dir: Path) -> bool:
    return "released" in {row.cls for row in records.lane_rows(run_dir).rows}


def _start_held(host: mcp_host.McpHost) -> tuple[int, Path, float]:
    """Send the one terminal call and hold it; return its request id, the run dir once the run's
    marker exists (the run is mid-wait), and the instant the call was sent (an upper bound on the
    run's admission)."""
    sent = time.monotonic()
    req = host.hold("run", _request())
    assert support.wait_until(
        lambda: bool(list((host.home / "runs").glob("*/r_*"))), STARTUP_WAIT_S
    ), "the run was never admitted"
    run_dir = _only_run_dir(host)
    assert support.wait_until(lambda: _issued(run_dir), STARTUP_WAIT_S), "no marker yet"
    return req, run_dir, sent


def _sever(host: mcp_host.McpHost, mode: str, req: int) -> None:
    if mode == "cancel_notification":
        host.sever(mode, req_id=req)
        with pytest.raises(RuntimeError, match="cancelled"):
            host.join(req, timeout=tolerances.JOIN_WAIT_S)
    elif mode == "stdin_close":
        host.sever(mode)
    else:
        host.sever(mode)
        host.proc.wait(timeout=tolerances.PROC_WAIT_S)


def _wait_server_gone_or_run_over(host: mcp_host.McpHost, run_dir: Path, sent: float) -> None:
    """Where the server is severed away, wait for the run's own end inside the bound: the server
    exits on its own (graceful stdin close), or is gone already (sigkill); either way the run's
    process tree must be over by deadline + margin from the send."""
    left = max(BOUND_S - (time.monotonic() - sent), 0.0)
    support.wait_until(lambda: host.proc.poll() is not None and _released(run_dir), left)


def test_pin_sever_today(tmp_path: Path) -> None:
    """The base behaviour the leaf keeps: a `cancel_notification` sever of a held `run` answers the
    held request with a JSON-RPC cancellation and does not stop the run, which reaches its own
    terminal state (S0; the drive is independent of the request)."""
    with mcp_host.McpHost(home=tmp_path / "host-home", timeout_s=HOST_TIMEOUT_S) as host:
        held = host.hold("run", {"plugin": "slow", "args": {"seconds": 1.0}, "wait_ms": 10_000})
        assert support.wait_until(
            lambda: bool(list((host.home / "runs").glob("*/r_*"))), STARTUP_WAIT_S
        )
        host.sever("cancel_notification", req_id=held)
        with pytest.raises(RuntimeError, match="cancelled"):
            host.join(held, timeout=tolerances.JOIN_WAIT_S)
        run_dir = _only_run_dir(host)
        assert support.wait_until(lambda: _terminal(run_dir) is not None, STARTUP_WAIT_S)
        assert _terminal(run_dir) == "succeeded"


@pytest.mark.proves("WR-TERM-6", "A1.2", "A", "single", "MCP", "CI")
@pytest.mark.parametrize("mode", SEVER_MODES)
def test_sever_run_continues_bounded_and_cleaned(tmp_path: Path, mode: str) -> None:
    with _host(tmp_path) as host:
        req, run_dir, sent = _start_held(host)
        run_id = run_dir.name
        with support.reaping(run_id):
            live = support.marked(run_id)
            assert live, "the run has no process to observe"
            _sever(host, mode, req)

            # the sever did not cancel: for a while after it the run is still waiting
            time.sleep(tolerances.SETTLE_LONG_S * 2)
            assert not _released(run_dir), "the run stopped when the call was severed"
            if mode == "cancel_notification":
                assert _terminal(run_dir) is None
                assert support.wait_until(
                    lambda: _terminal(run_dir) is not None,
                    BOUND_S - (time.monotonic() - sent),
                ), "the run did not end inside deadline + margin"
            else:
                _wait_server_gone_or_run_over(host, run_dir, sent)
                assert host.proc.poll() is not None
                if _terminal(run_dir) is None:
                    # the server is gone: the next server on this home recovers the run
                    host.restart()

            # the run ended, not by a cancel, and no process of its tree is left
            elapsed = time.monotonic() - sent
            assert support.wait_until(lambda: _terminal(run_dir) is not None, BOUND_S)
            assert not support.alive_marked(live, run_id), "the run's process tree was left"
            assert _released(run_dir), "the created marker was not released"
            causes = [row["cause"] for row in support.rows_of(run_dir, "stop_row")]
            assert "cancel" not in causes, causes
            if mode != "sigkill":
                assert causes == ["release_point"], causes
                assert elapsed <= BOUND_S, elapsed


@pytest.mark.proves("WR-TERM-6", "A1.2", "A", "single", "MCP", "CI")
@pytest.mark.parametrize("mode", SEVER_MODES)
def test_resend_joins_single_execution_terminal(tmp_path: Path, mode: str) -> None:
    with _host(tmp_path) as host:
        req, run_dir, sent = _start_held(host)
        run_id = run_dir.name
        with support.reaping(run_id):
            _sever(host, mode, req)
            if mode != "cancel_notification":
                # a new session on the home: wait for the old server to be gone first
                support.wait_until(lambda: host.proc.poll() is not None, BOUND_S)
                assert host.proc.poll() is not None
                host.restart()
            answer = host.call("run", _request())
            assert isinstance(answer, dict), answer
            # the one call returns the terminal answer of the one execution, never a running frame
            assert answer["run_id"] == run_id, answer
            assert answer["state"] not in NONTERMINAL, answer
            assert answer["state"] == _terminal(run_dir), answer
            assert answer["answer"]["cleanup"]["clean"] is True, answer
            assert _created_rows(run_dir) == 1
            assert len(list((host.home / "runs").glob("*/r_*"))) == 1
