"""PluginSurface — script-facing contract."""

from __future__ import annotations

from collections.abc import Callable, Collection, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol, TypeVar, overload

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
    def event(self, kind: str, **fields: Any) -> None: ...


@overload
def trestle(fn: F, /) -> F: ...  # noqa: UP047


@overload
def trestle(
    *,
    deadline: timedelta | float | None = None,
    summary_fields: Sequence[str] = (),
    packages: Sequence[str] = (),
    env_arg: str | None = None,
    secrets: Collection[str] = (),
    repeatable: bool = False,
) -> Callable[[F], F]: ...


def trestle(  # noqa: UP047
    fn: F | None = None,
    /,
    *,
    deadline: timedelta | float | None = None,
    summary_fields: Sequence[str] = (),
    packages: Sequence[str] = (),
    env_arg: str | None = None,
    secrets: Collection[str] = (),
    repeatable: bool = False,
) -> F | Callable[[F], F]:
    """Mark a callable as a Trestle plugin entry point.

    Bare (`@trestle`) or call form (`@trestle(deadline=..., summary_fields=..., packages=...,
    env_arg=..., secrets=..., repeatable=...)`). The metadata is read statically from the source at
    publication (literals only), never from this call; at run time the call form marks the
    callable exactly as the bare form does.
    """

    def mark(target: F) -> F:
        target.__trestle_plugin__ = True  # type: ignore[attr-defined]
        return target

    if fn is not None:
        return mark(fn)
    return mark


def is_trestle_plugin(fn: Callable[..., Any]) -> bool:
    return bool(getattr(fn, "__trestle_plugin__", False))
