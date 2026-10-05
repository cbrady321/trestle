"""Operator actions — cancel, pin, unpin, join projection (not admit/run)."""

from __future__ import annotations

from typing import Any

from trestle.common.types import RequestOutcome, RunView
from trestle.server.main import Kernel


def cancel_run(kernel: Kernel, handle: str) -> RequestOutcome:
    return kernel.control.cancel(handle)


def pin_retention(kernel: Kernel, handle: str) -> RequestOutcome:
    return kernel.control.pin(handle)


def unpin_retention(kernel: Kernel, handle: str) -> RequestOutcome:
    return kernel.control.unpin(handle)


def join_waits(
    kernel: Kernel,
    handles: list[str],
    mode: str = "all",
    timeout_ms: int = 2000,
) -> list[RunView] | RequestOutcome:
    return kernel.control.await_runs(handles, mode=mode, timeout_ms=timeout_ms)


def join_waits_to_dict(
    result: list[RunView] | RequestOutcome,
) -> dict[str, Any] | RequestOutcome:
    if isinstance(result, RequestOutcome):
        return result
    return {"items": [view.to_dict() for view in result]}
