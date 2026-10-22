"""PluginSurface — script-facing contract."""

from __future__ import annotations

import re
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol, TypeVar, overload

F = TypeVar("F", bound=Callable[..., Any])


# What `Blocked.resend` may say about sending the same request again (the tree's `Resend` values).
RESEND_VALUES: tuple[str, ...] = ("succeeds_after_action", "will_not_succeed", "unknown")
_BLOCKED_CODE = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)*")


class Blocked(Exception):  # noqa: N818 - the outcome's name, as the workflow `Blocked` step is
    """Raise from a plain plugin when a person must act before it can go on (R4a): an expired
    login, a missing tool, a prompt nobody will answer. The run ends with outcome `blocked`, and
    its answer carries `code` (recorded as `plugin.<code>`, so it never reads as one of
    Trestle's own codes), the one `human_action` that unblocks it, and whether re-sending the
    same request will then succeed (`resend`: "succeeds_after_action", "will_not_succeed" or
    "unknown")."""

    def __init__(self, code: str, human_action: str, resend: str = "succeeds_after_action") -> None:
        if not isinstance(code, str) or not _BLOCKED_CODE.fullmatch(code):
            raise ValueError(
                f"Blocked code {code!r}: lower-case words joined by '_' or '.', like 'aws_login'"
            )
        if not isinstance(human_action, str) or not human_action.strip():
            raise ValueError("Blocked needs the human action that unblocks the run")
        if resend not in RESEND_VALUES:
            raise ValueError(f"Blocked resend must be one of {RESEND_VALUES}, got {resend!r}")
        super().__init__(f"{code}: {human_action}")
        self.code = code
        self.human_action = human_action
        self.resend = resend


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
    def credential_env(self, name: str) -> dict[str, str]: ...
    def credential_file(self, name: str) -> Path: ...


@overload
def trestle(fn: F, /) -> F: ...  # noqa: UP047


@overload
def trestle(
    *,
    deadline: timedelta | float | None = None,
    summary_fields: Sequence[str] = (),
    packages: Sequence[str] = (),
    env_arg: str | None = None,
    credentials: Sequence[str] = (),
    secrets: Collection[str] = (),
    repeatable: bool = False,
    stop_grace: float | None = None,
) -> Callable[[F], F]: ...


def trestle(  # noqa: UP047
    fn: F | None = None,
    /,
    *,
    deadline: timedelta | float | None = None,
    summary_fields: Sequence[str] = (),
    packages: Sequence[str] = (),
    env_arg: str | None = None,
    credentials: Sequence[str] = (),
    secrets: Collection[str] = (),
    repeatable: bool = False,
    stop_grace: float | None = None,
) -> F | Callable[[F], F]:
    """Mark a callable as a Trestle plugin entry point.

    Bare (`@trestle`) or call form (`@trestle(deadline=..., summary_fields=..., packages=...,
    env_arg=..., credentials=..., secrets=..., repeatable=..., stop_grace=...)`). The metadata is
    read statically from the source at publication (literals only), never from this call; at run
    time the call form marks the callable exactly as the bare form does.

    `credentials` names the identities the plugin uses (`ctx.credential_env`,
    `ctx.credential_file`); the operator configures and grants each. `secrets` (secret
    arguments, whose values the agent sends) is deprecated in favour of `credentials`.
    """

    def mark(target: F) -> F:
        target.__trestle_plugin__ = True  # type: ignore[attr-defined]
        return target

    if fn is not None:
        return mark(fn)
    return mark


def is_trestle_plugin(fn: Callable[..., Any]) -> bool:
    return bool(getattr(fn, "__trestle_plugin__", False))
