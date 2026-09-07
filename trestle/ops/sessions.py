"""Operator session rows — ControlSurface.query projection."""

from __future__ import annotations

from typing import Any

from trestle.common.types import RequestOutcome
from trestle.server.main import Kernel


def read_session_rows(
    kernel: Kernel,
    *,
    view: str,
    params: dict[str, Any] | None = None,
    cursor: str | None = None,
) -> dict[str, Any] | RequestOutcome:
    kernel.registry.maybe_refresh()
    return kernel.control.query(view, params, cursor)
