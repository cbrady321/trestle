"""Abstract query backend contract."""

from __future__ import annotations

from typing import Any, Protocol

from trestle.common.types import Handle, RequestOutcome


class QueryBackend(Protocol):
    backend: str

    def query(
        self,
        view: str,
        params: dict[str, object],
        cursor: Handle | None = None,
    ) -> dict[str, Any] | RequestOutcome:
        """Return a BoundedView envelope or RequestOutcome."""
