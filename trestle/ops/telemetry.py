"""Operator telemetry chunk — ControlSurface.fetch projection."""

from __future__ import annotations

from typing import Any

from trestle.common.types import RequestOutcome
from trestle.server.main import Kernel


def read_telemetry_chunk(
    kernel: Kernel,
    *,
    handle: str,
    window: dict[str, Any],
) -> dict[str, Any] | RequestOutcome:
    return kernel.control.fetch(handle, window)
