"""PluginSurface — script-facing contract."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, TypeVar

F = TypeVar("F", bound=Callable[..., Any])


class Context(Protocol):
    tmp: Path
    outputs: Path
    cancelled: bool
    deadline: datetime

    def log(self, message: str) -> None: ...
    def progress(self, message: str, *, fraction: float | None = None) -> None: ...
    def artifact(self, name: str) -> Path: ...
    def attach(self, path: Path, *, name: str) -> str: ...


def trestle(fn: F) -> F:  # noqa: UP047
    """Mark a callable as a Trestle plugin entry point."""
    fn.__trestle_plugin__ = True  # type: ignore[attr-defined]
    return fn


def is_trestle_plugin(fn: Callable[..., Any]) -> bool:
    return bool(getattr(fn, "__trestle_plugin__", False))
