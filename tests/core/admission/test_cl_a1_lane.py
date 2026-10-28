"""CL-A1.1 the admission lane (MC-30, SA-12): the registry refresh and `Admission.admit` run on one
serialized admission thread, never on the event loop.

Every timing bound comes from `tests.proof.tolerances` (SA-05); no timing literal appears here.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import Any

import pytest
from fastmcp import FastMCP

from tests.core.spine import support
from tests.proof import mcp_host, tolerances
from trestle.common import codes
from trestle.common.types import AdmitRequest, RequestOutcome, RunView
from trestle.server.control import AdmissionLane
from trestle.server.main import attach_registry_version_mirror

# The number of callers that present one idempotency key at once.
N_CALLERS = 16


def _run_dirs(home: Path) -> list[Path]:
    return sorted((home / "runs").glob("*/r_*"))


def _created_rows(run_dir: Path) -> int:
    ledger = run_dir / "evidence" / "ledger.ndjson"
    return sum(1 for line in ledger.read_text(encoding="utf-8").splitlines() if '"created"' in line)


@pytest.mark.proves(
    "WR-IDEM-2", "WR-IDEM-2:concurrent-single-run", "core", "core", "MCP+PROC", "CI"
)
def test_n_concurrent_identical_admissions_one_run(tmp_path: Path) -> None:
    args = {"plugin": "echo", "args": {"message": "x"}, "idempotency_key": "cl-a1-one-key"}
    with mcp_host.McpHost(home=tmp_path / "host-home") as host:
        held = [host.hold("run", {**args, "wait_ms": 0}) for _ in range(N_CALLERS)]
        answers = [host.join(req, timeout=tolerances.HARNESS_WAIT_MS / 1000) for req in held]
        run_ids = {answer["run_id"] for answer in answers}
        assert len(run_ids) == 1, answers
        dirs = _run_dirs(host.home)
        assert [path.name for path in dirs] == list(run_ids)
        assert _created_rows(dirs[0]) == 1


@pytest.mark.proves(
    "WR-IDEM-2",
    "WR-IDEM-2:different-intent-refused-no-run-id",
    "core",
    "core",
    "MCP+PROC",
    "CI",
)
def test_different_intent_under_one_key_is_refused_without_a_run(tmp_path: Path) -> None:
    key = "cl-a1-intent-key"
    with mcp_host.McpHost(home=tmp_path / "host-home") as host:
        first = host.call(
            "run",
            {"plugin": "echo", "args": {"message": "a"}, "idempotency_key": key, "wait_ms": 0},
        )
        assert "run_id" in first, first
        others = [
            host.hold(
                "run",
                {
                    "plugin": "echo",
                    "args": {"message": f"other-{n}"},
                    "idempotency_key": key,
                    "wait_ms": 0,
                },
            )
            for n in range(N_CALLERS)
        ]
        refusals = [host.join(req, timeout=tolerances.HARNESS_WAIT_MS / 1000) for req in others]
        for refusal in refusals:
            assert refusal["code"] == codes.IDEMPOTENCY_KEY_CONFLICT, refusal
            assert "run_id" not in refusal, refusal
        assert [path.name for path in _run_dirs(host.home)] == [first["run_id"]]


class _Recorder:
    """The threads `maybe_refresh` and `admit` ran on."""

    def __init__(self) -> None:
        self.refresh: list[int] = []
        self.admit: list[int] = []
        self.names: set[str] = set()

    def wrap(self, target: object, name: str, into: list[int]) -> None:
        real = getattr(target, name)

        def wrapped(*args: Any, **kwargs: Any) -> Any:
            into.append(threading.get_ident())
            self.names.add(threading.current_thread().name)
            return real(*args, **kwargs)

        setattr(target, name, wrapped)


def test_single_admission_thread() -> None:
    kernel = support.spine_kernel()
    recorder = _Recorder()
    recorder.wrap(kernel.registry, "maybe_refresh", recorder.refresh)
    recorder.wrap(kernel.control.admission, "admit", recorder.admit)

    async def scenario() -> tuple[int, list[RunView | RequestOutcome]]:
        views = await asyncio.gather(
            *(
                kernel.control.run_async(
                    plugin="echo", args={"message": str(n)}, wait_ms=tolerances.HARNESS_WAIT_MS
                )
                for n in range(4)
            )
        )
        # the tools/list hook refreshes through the lane too
        mcp = FastMCP("cl-a1-lane")
        attach_registry_version_mirror(mcp, kernel)
        await mcp._list_tools_mcp(None)  # type: ignore[arg-type]
        return threading.get_ident(), list(views)

    loop_thread, views = asyncio.run(scenario())
    assert all(isinstance(view, RunView) and view.state == "succeeded" for view in views)
    assert recorder.admit and recorder.refresh
    lane_threads = set(recorder.refresh) | set(recorder.admit)
    assert len(lane_threads) == 1, "refresh and admit must share the one admission thread"
    assert loop_thread not in lane_threads
    assert recorder.names == {f"{AdmissionLane.THREAD_NAME}_0"}


def test_admissions_are_serialized_and_ordered(monkeypatch: pytest.MonkeyPatch) -> None:
    """Submitted admissions run one at a time, in submission order."""
    kernel = support.spine_kernel()
    order: list[str] = []
    gate = threading.Event()
    real = kernel.control.admission.admit

    def slow_admit(request: AdmitRequest) -> Any:
        order.append(f"in:{request.args['message']}")
        if request.args["message"] == "first":
            gate.wait(tolerances.JOIN_WAIT_S)
        result = real(request)
        order.append(f"out:{request.args['message']}")
        return result

    monkeypatch.setattr(kernel.control.admission, "admit", slow_admit)
    first = kernel.control.submit_admit(AdmitRequest(plugin="echo", args={"message": "first"}))
    second = kernel.control.submit_admit(AdmitRequest(plugin="echo", args={"message": "second"}))
    assert not second.done()
    gate.set()
    assert first.result(tolerances.JOIN_WAIT_S).tag == "admitted"
    assert second.result(tolerances.JOIN_WAIT_S).tag == "admitted"
    assert order == ["in:first", "out:first", "in:second", "out:second"]
