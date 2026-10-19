"""PluginSurface — script-facing contract."""

from __future__ import annotations

from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol, TypeVar, overload

F = TypeVar("F", bound=Callable[..., Any])


@dataclass(frozen=True)
class ExecResult:
    """What `Context.exec` returns for one command.

    `returncode` is None when the command did not finish on its own: it `timed_out` (its
    per-call timeout passed) or was `cancelled` (the run was stopped); its process group was
    killed either way. `stdout` and `stderr` are what was kept of each stream (decoded as UTF-8,
    bad bytes replaced); `truncated` says a stream passed its cap, and `output_bytes` counts
    everything both streams produced. `artifact` is the handle of the kept output's artifact, or
    the empty string when none was kept."""

    argv: tuple[str, ...]
    returncode: int | None
    timed_out: bool
    cancelled: bool
    duration_s: float
    stdout: str
    stderr: str
    output_bytes: int
    truncated: bool
    artifact: str

    @property
    def ok(self) -> bool:
        """The command ran to its end and exited 0."""
        return self.returncode == 0


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
    def remaining(self) -> float: ...
    def exec(
        self,
        argv: Sequence[str],
        *,
        timeout: float | None = None,
        cwd: Path | str | None = None,
        env: Mapping[str, str | None] | None = None,
        input: str | bytes | None = None,
        output: str | None = "exec.log",
        max_output_bytes: int = 1024 * 1024,
        credentials: Sequence[str] | None = None,
    ) -> ExecResult: ...


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
